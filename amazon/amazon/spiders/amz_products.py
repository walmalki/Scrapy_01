import scrapy
from selenium import webdriver
from scrapy.crawler import CrawlerProcess
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from datetime import datetime, timedelta
import json
import os
import re
import random
import requests

# Specify ASINs to scrape directly (Prioritized)
specific_asins = []  # If empty, the script will load ASINs from the input file

# Define other configurations
INPUT_FILE = "data/amz_asin.jsonl"  # JSONLines format
MAX_PRODUCTS = 2000
OUTPUT_DIR = "data"

# Global variables to hold data
data_collected = []  # Store scraped data globally

# Load credentials from config.json
with open("config.json", "r") as config_file:
    config = json.load(config_file)

# Extract credentials
SCRAPEOPS_API_KEY = config.get("SCRAPEOPS_API_KEY")

if not SCRAPEOPS_API_KEY:
    raise ValueError("API key is missing from the configuration file.")

# Function to get a random user agent
def get_scrapeops_fake_user_agent():
    """Fetch a random user agent from ScrapeOps API"""
    try:
        # Make the request to ScrapeOps API with the API key
        url = f"http://headers.scrapeops.io/v1/user-agents?api_key={SCRAPEOPS_API_KEY}"
        response = requests.get(url)

        if response.status_code == 200:
            return response.json().get("result", ["Mozilla/5.0"])[0]  # Default fallback
    except Exception:
        return "Mozilla/5.0"

USER_AGENT = get_scrapeops_fake_user_agent()

# Function to get random browser headers
def get_scrapeops_fake_headers():
    """Fetch random browser headers from ScrapeOps API with a fallback if none are returned."""
    try:
        # Make the request to ScrapeOps API with the API key
        url = f"http://headers.scrapeops.io/v1/browser-headers?api_key={SCRAPEOPS_API_KEY}"
        response = requests.get(url)

        if response.status_code == 200:
            data = response.json()
            headers = data.get("result")
            # If headers is a list, try to use the first element if available
            if isinstance(headers, list):
                if headers:  # non-empty list
                    return headers[0]
                else:
                    return {"User-Agent": "Mozilla/5.0"}  # fallback
            # If headers is a dict, return it directly
            elif isinstance(headers, dict):
                return headers
        # If the API call did not return a 200 or headers is not found, return fallback
        return {"User-Agent": "Mozilla/5.0"}
    except Exception:
        return {"User-Agent": "Mozilla/5.0"}

FAKE_HEADERS = get_scrapeops_fake_headers()

# If FAKE_HEADERS is empty or None, use a fallback
if not FAKE_HEADERS:
    FAKE_HEADERS = {'User-Agent': 'Mozilla/5.0'}  # Default user-agent header

# Check the structure of the headers
if isinstance(FAKE_HEADERS, dict):
    FAKE_HEADERS = {k: str(v) for k, v in FAKE_HEADERS.items()}  # Ensure all values are strings
else:
    FAKE_HEADERS = {}

# Define the Spider
class AmazonProductsSpider(scrapy.Spider):
    name = "amz_products"

    # Custom settings for the spider
    custom_settings = {
        'LOG_LEVEL': 'INFO',
        'CONCURRENT_REQUESTS': 1,
        'DOWNLOAD_DELAY': random.uniform(1, 2),  # Random delay to avoid detection
        'FEED_EXPORT_ENCODING': 'utf-8',
        'DEPTH_PRIORITY': 1,  # Give priority to deeper pages (pagination)
        'SCHEDULER_DISK_QUEUE': 'scrapy.squeues.PickleFifoDiskQueue',
        'SCHEDULER_MEMORY_QUEUE': 'scrapy.squeues.FifoMemoryQueue',
        'DEFAULT_REQUEST_HEADERS': {},  # Use ScrapeOps Fake Headers
        'SCRAPEOPS_API_KEY': SCRAPEOPS_API_KEY,  # Your ScrapeOps API key
        'SCRAPEOPS_FAKE_USER_AGENT_ENABLED': True,  # Enable fake user agent
        'SCRAPEOPS_FAKE_HEADERS_ENABLED': True,  # Enable the proxy
        'SCRAPEOPS_PROXY_ENABLED': True,  # Enable the proxy

        # Enable AutoThrottle settings
        'AUTOTHROTTLE_ENABLED': True,
        'AUTOTHROTTLE_START_DELAY': 5,  # Start with 5 seconds delay between requests
        'AUTOTHROTTLE_MAX_DELAY': 60,  # Max delay between requests is 1 minute
        'AUTOTHROTTLE_TARGET_CONCURRENCY': 1.0,  # Number of requests to fetch concurrently
        'AUTOTHROTTLE_DEBUG': False,  # Set to True for debugging AutoThrottle behavior
    }

    # Function to initialize the Spider
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.product_count = 0
        self.specific_asins = specific_asins
        
        if self.specific_asins:
            self.logger.info("Using specific ASINs provided. Skipping all prompts.")
            self.asins_to_scrape = self.specific_asins  # ✅ Highest Priority
        else:
            self.logger.info("No specific ASINs provided. Proceeding with prompt-based loading.")
            self.asins_to_scrape = self._load_asins_with_serial_filter_or_prompt()  # ✅ Second Priority

        self.output_file = self._generate_output_filename()
        
        # ✅ Ensure start_serial_number is always initialized
        self.start_serial_number = self._get_last_serial_number(log_message=False)  # ✅ Prevent duplicate log and ✅ No logging during initialization

        # Initialize Selenium WebDriver
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-extensions")  # Disable extensions
        chrome_options.add_argument("--disable-software-rasterizer")  # Disable software rasterizer
        chrome_options.add_argument("--blink-settings=imagesEnabled=false")  # Disable images
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument(f"user-agent={USER_AGENT}")  # Use the dynamic ScrapeOps User-Agent
        self.driver = webdriver.Chrome(options=chrome_options)

    # Function to prompt the user to filter by serial number range or resume from the last stop
    def _load_asins_with_serial_filter_or_prompt(self):
        """Load ASINs from the input file, allow filtering by serial number range, 
        and resume from last stop if chosen."""
        try:
            asins = []
            serial_numbers = []

            with open(INPUT_FILE, "r", encoding="utf-8") as file:
                for line in file:
                    record = json.loads(line)
                    serial_numbers.append(record.get("serial_number", ""))
                    if record.get("re_scrape", True):
                        asins.append(record["asin"])

            # ✅ Ask if the user wants to filter by serial number range
            use_range = input("Do you want to filter by serial number range? (yes/no): ").strip().lower()

            if use_range == "yes":
                start_sn = input("Enter start serial number (e.g., ASN50): ").strip()
                end_sn = input("Enter end serial number (e.g., ASN100): ").strip()

                if start_sn in serial_numbers and end_sn in serial_numbers:
                    start_idx = serial_numbers.index(start_sn)
                    end_idx = serial_numbers.index(end_sn)
                    asins = asins[start_idx:end_idx + 1]
                    self.logger.info(f"🌚 Filtered ASINs from serial {start_sn} to {end_sn}")
                else:
                    self.logger.warning("⚠️ Invalid serial number range provided. Using all ASINs.")

            else:
                # ✅ Ask if the user wants to continue from the last stop
                choice = input("Do you want to continue from the last stop? (yes/no): ").strip().lower()
                if choice == "yes":
                    last_serial_number = self._get_last_serial_number(log_message=True)  # Get last scraped serial (e.g., 1294)

                    if last_serial_number > 0:  # ✅ Ensure valid last serial
                        numeric_serial = last_serial_number  # ✅ Use it directly (already an integer)

                        # ✅ Find the corresponding ASIN position in `amz_asin.jsonl`
                        last_serial_index = serial_numbers.index(f"ASN{numeric_serial}") if f"ASN{numeric_serial}" in serial_numbers else -1
                        if last_serial_index != -1:
                            asins = asins[last_serial_index + 1:]  # ✅ Resume from the next ASIN
                            self.logger.info(f"🚣 Resuming from serial number ASN{numeric_serial + 1}")
                        else:
                            self.logger.warning(f"☢️ Could not find ASN{last_serial_number} in the input file. Starting from the beginning.")

            if not asins:
                self.logger.error("⚠️ No ASINs found to scrape. Exiting...")
                exit(1)  # ✅ Exit gracefully if no ASINs are found

            return asins

        except (FileNotFoundError, json.JSONDecodeError) as e:
            self.logger.error(f"⚠️ Error loading ASINs: {e}")
            exit(1)  # ✅ Exit if an error occurs

    # function to generate the output filename
    def _generate_output_filename(self):
        """Generate a unique output filename based on the current GMT+3 date and time."""
        now = datetime.utcnow() + timedelta(hours=3)
        timestamp = now.strftime("%d-%m-%Y_%I-%M-%p")
        return os.path.join(OUTPUT_DIR, f"amz_products_{timestamp}.json")

    # function to get the last serial number
    def _get_last_serial_number(self, log_message=True):
        """Find the last serial number from the most recent output file and return an integer."""
        try:
            files = [f for f in os.listdir(OUTPUT_DIR) if f.startswith("amz_products_") and f.endswith(".json")]
            if not files:
                return 0  # ✅ Default to integer 0 if no previous data exists

            files.sort(key=lambda f: os.path.getctime(os.path.join(OUTPUT_DIR, f)), reverse=True)
            latest_file = os.path.join(OUTPUT_DIR, files[0])

            with open(latest_file, "r", encoding="utf-8") as file:
                existing_data = json.load(file)
                if isinstance(existing_data, list) and len(existing_data) > 0:
                    last_serial = max(
                        int(re.search(r"\d+", item["serial_number"]).group()) for item in existing_data
                    )
                    if log_message:  # ✅ Log only when needed
                        self.logger.info(f"🌗 Last serial number found: AP{last_serial}")
                    return last_serial  # ✅ Return as an integer (not a string with "AP")
        except Exception as e:
            self.logger.error(f"⚠️ Error reading last serial number: {e}")
        return 0  # ✅ Ensure integer fallback

    # function to start scraping
    def start_requests(self):
        """Start scraping process. No duplicate prompt for continuing from the last stop."""
        base_url = "https://www.amazon.sa/dp/"

        # If no ASINs to scrape, log an error and exit early
        if not self.asins_to_scrape:
            self.logger.error("⚠️ No ASINs available to scrape. Exiting...")
            return

        # Start Scraping
        for asin in self.asins_to_scrape:
            if self.product_count >= MAX_PRODUCTS:
                break
            url = f"{base_url}{asin}?language=en_AE&th=1&psc=1"
            headers = {"User-Agent": random.choice(USER_AGENT)}
            yield scrapy.Request(url, callback=self.parse_product_details, meta={"asin": asin}, headers=headers)

    # function to parse product details
    def parse_product_details(self, response):
        global data_collected
        product_details = {}

        if self.product_count >= MAX_PRODUCTS:
            return

        self.product_count += 1
        asin = response.meta["asin"]

        self.logger.info(f"😘 Processing ASIN: {asin}")
        
        try:
            # Increment serial number
            self.start_serial_number = int(self.start_serial_number)  # ✅ Convert to integer to ensure self.start_serial_number is an integer before using it
            self.start_serial_number += 1
            serial_number = f"AP{self.start_serial_number}"  # ✅ Safe string concatenation
            self.logger.info(f"💯 Serial Number: {serial_number}")

            # Scraping date
            scraping_date = datetime.utcnow().strftime("%d/%m/%Y")
            self.logger.info(f"📅 Current Scraping Date: {scraping_date}")

            # Convert UTC to Saudi Arabia Time (AST, UTC+3)
            saudi_time = datetime.utcnow() + timedelta(hours=3)
            scraping_time = saudi_time.strftime("%I:%M %p")
            self.logger.info(f"🕰️ Current Scraping Time: {scraping_time}")

            # Extracting the title of the product
            product_title = response.css("span#productTitle::text").get(default="").strip()
            self.logger.info(f"📕 Extracting Product Title: {product_title}")

            # Availability extraction
            availability_selectors = [
                "div#availability span::text",  # Main availability section
                "div#availability div.a-section span::text",  # Alternative availability text
                "#availabilityInsideBuyBox_feature_div span::text",  # Availability inside buy box
                "#desktop_buybox div.a-section span::text",  # Another alternative location
            ]

            availability_text = None
            for selector in availability_selectors:
                extracted_text = response.css(selector).get()
                if extracted_text:
                    availability_text = extracted_text.strip()
                    break

            if availability_text:
                availability_number = re.search(r"\d+", availability_text)
                availability = availability_number.group() if availability_number else "Available"
            else:
                availability = "Currently unavailable"

            # If product is unavailable, set price to "N/A"
            if "currently unavailable" in availability.lower():
                price = "N/A"
            else:
                # Extract current price only if product is available
                price_selectors = [
                    "#corePrice_feature_div .a-spacing-micro span span::text",
                    ".apexPriceToPay span::text",
                    ".priceToPay span span::text",
                    "#corePrice_feature_div span span::text",
                    "span.a-price span.a-offscreen::text",
                    "span#price_inside_buybox::text",
                    "span#newBuyBoxPrice::text",
                    "span.priceToPay span.a-offscreen::text",
                ]

                price = None
                for selector in price_selectors:
                    raw_price = response.css(selector).get()
                    if raw_price:
                        price = re.sub(r"SAR", "", raw_price).strip()
                        break

                # Ensure price has a fallback value
                price = price if price else "N/A"

            # Log the final extracted price
            self.logger.info(f"💸 Extracted price: {price}")

            # Extract previous price
            previous_price_selectors = [
                ".basisPrice span span::text",
                "span.a-price.a-text-price span.a-offscreen::text",
                "span#priceblock_dealprice::text",
            ]
            raw_previous_price = None

            for selector in previous_price_selectors:
                raw_previous_price = response.css(selector).get()
                if raw_previous_price:
                    break

            previous_price = re.sub(r"SAR", "", raw_previous_price).strip() if raw_previous_price else "N/A"
            self.logger.info(f"🏷️ Extracted previous price: {previous_price}")

            # Extracting Discount
            raw_discount = response.css("span.savingsPercentage::text").get(default="0%").strip()
            discount = re.sub(r"[\u200e]", "", raw_discount).strip()
            self.logger.info(f"🔖 Extracted discount: {discount}")

            # Handle cases where price and previous price are identical or discount is 0%
            if previous_price == price or discount == "0%":
                # self.logger.warning(f"Previous price is incorrect (either matches current price or discount is 0%). Setting previous price to 'N/A'.")
                previous_price = "N/A"

            # Extracting Currency
            currency = response.css("#corePrice_feature_div .a-price-symbol::text").get(default="N/A").strip()
            self.logger.info(f"💲 Extracted Currency: {currency}")

            # Extracting Product link
            product_link = response.url
            self.logger.info(f"🔗 Extracted Product link: {product_link}")

            # Extracting Category
            category_list = response.css("div#wayfinding-breadcrumbs_feature_div ul.a-unordered-list li a::text").getall()
            category_list = [cat.strip() for cat in category_list if cat.strip()]  # Clean whitespace and filter empty items

            # Ensure we always have 5 category slots (fill missing ones with "N/A")
            category_01, category_02, category_03, category_04, category_05, category_06 = (category_list + ["N/A"] * 6)[:6]
            self.logger.info(f"📊 Extracted Categories: {category_list}")

            # Sold by, Sold by link and seller ID extraction
            sold_by = "Amazon.sa" if "Amazon.sa" in response.css("#merchantInfoFeature_feature_div .offer-display-feature-text-message::text").get(default="N/A") else response.css("a#sellerProfileTriggerId::text").get(default="N/A")
            sold_by_link = response.css("a#sellerProfileTriggerId::attr(href)").get()
            if sold_by_link:
                sold_by_link = "https://www.amazon.sa" + sold_by_link
                seller_id_match = re.search(r"seller=([A-Z0-9]+)", sold_by_link)
                seller_id = seller_id_match.group(1) if seller_id_match else ""
            else:
                sold_by_link = "N/A"
                seller_id = "N/A"

            self.logger.info(f"🛒 Extracted sold_by: {sold_by}")
            self.logger.info(f"🔗 Extracted sold_by_link: {sold_by_link}")
            self.logger.info(f"🆔 Extracted seller_id: {seller_id}")

            # Extract number of sellers
            try:
                sellers_text = response.css('span.a-color-base:contains("New")::text').get()

                if sellers_text:
                    # Using regex to extract the number from the "New (X) from" text
                    sellers_match = re.search(r"New \((\d+)\) from", sellers_text)
                    if sellers_match:
                        num_sellers = sellers_match.group(1)
                    else:
                        num_sellers = "1"  # If regex doesn't find a match, assume only 1 seller
                else:
                    num_sellers = "1"  # Default to 1 seller if no match found

                # Log the number of sellers
                self.logger.info(f"🛒 Extracted number of sellers: {num_sellers}")
            except Exception as e:
                self.logger.error(f"⚠️ Error extracting number of sellers: {e}")
                num_sellers = "1"  # Default to 1 if there's an error

            # Add the number of sellers to the product details
            product_details["Sellers"] = num_sellers

            # Extract "Fulfilled by" information
            try:
                fulfilled_by_text = response.css('span.offer-display-feature-text-message::text').get()

                if fulfilled_by_text:
                    fulfilled_by = fulfilled_by_text.strip()  # Clean up extra spaces if any
                else:
                    fulfilled_by = "N/A"  # Default value if nothing is found

                # Log the extracted "Fulfilled by"
                self.logger.info(f"📦 Extracted fulfilled by: {fulfilled_by}")

            except Exception as e:
                self.logger.error(f"⚠️ Error extracting Fulfilled by: {e}")
                fulfilled_by = "N/A"  # Default value in case of an error

            # Add the Fulfilled by data to the product details
            product_details["Fulfilled_by"] = fulfilled_by

            # Extract and clean brand name
            try:
                raw_brand = response.css("a#bylineInfo::text").get(default="").strip()
                if raw_brand:
                    # Remove unwanted texts like "Visit the", "Store", and "Brand:"
                    brand = re.sub(r"(Visit the|Store|Brand:)", "", raw_brand).strip()
                else:
                    brand = "N/A"
                self.logger.info(f"💝 Extracted brand: {brand}")

            except Exception as e:
                self.logger.error(f"⚠️ Error extracting brand: {e}")

            # After extracting sold_by and fulfilled_by
            try:
                # Logic for determining fulfillment type
                if sold_by != "Amazon" and fulfilled_by == "Amazon":
                    fulfillment_type = "FBA"  # Fulfilled by Amazon, sold by a third party
                elif sold_by == "Amazon" and fulfilled_by == "Amazon":
                    fulfillment_type = "AMZ"  # Sold and fulfilled by Amazon
                elif sold_by != "Amazon" and fulfilled_by == sold_by:
                    fulfillment_type = "FBM"  # Fulfilled and sold by the same third-party seller
                else:
                    fulfillment_type = "N/A"  # Default value if no matching condition

                # Log the determined fulfillment type
                self.logger.info(f"🚚 Fulfillment Type: {fulfillment_type}")

            except Exception as e:
                self.logger.error(f"⚠️ Error determining fulfillment type: {e}")
                fulfillment_type = "N/A"  # Default value in case of an error

            # Add the fulfillment_type data to the product details
            product_details["fulfillment_type"] = fulfillment_type

            # Extract bought_in_past_month and clean the text
            try:
                raw_bought = response.css("#social-proofing-faceout-title-tk_bought .a-text-bold::text").get(default="0")
                match = re.search(r"(\d+\+)", raw_bought)
                bought_in_past_month = match.group(1) if match else "0"
            except Exception as e:
                bought_in_past_month = "0"
                self.logger.error(f"⚠️ Error extracting bought_in_past_month: {e}")
            self.logger.info(f"🏛️ Extracted bought in past month: {bought_in_past_month}")

            # Extract images link
            image_link = response.css("img#landingImage::attr(src)").get()
            self.logger.info(f"🖼️ Extracted Image Link: {image_link}")

            # Extract Total Rating
            raw_total_rating = response.css("span#acrCustomerReviewText::text").get(default="0")
            total_rating = re.sub(r"[^\d]", "", raw_total_rating).strip()  # Remove non-numeric characters (like commas)
            total_rating = total_rating if total_rating else "0"  # Ensure it's not empty
            
            # Extract Star Rating
            star_rating = response.css("span.a-icon-alt::text").re_first(r"([0-5]\.\d?)")
            # If total_rating is "0", set star_rating to "0"
            if total_rating == "0":
                star_rating = "0"
            else:
                star_rating = star_rating.strip() if star_rating else "0"

            self.logger.info(f"⭐ Extracted Star Rating: {star_rating}")           
            self.logger.info(f"💹 Extracted Total Rating: {total_rating}")

            # Log the extracted Availability
            self.logger.info(f"📦 Extracted availability: {availability}")
            
            # Refresh Selenium Driver for each ASIN
            self.driver.get(response.url)

            try:
                # Wait for the Best Sellers Rank section to load
                WebDriverWait(self.driver, 30).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "#productDetails_detailBullets_sections1, #detailBulletsWrapper_feature_div"))
                )
                self.logger.info("😯 Best Sellers Rank section loaded.")

                # Selenium for Best Sellers Rank extraction
                best_sellers_rank_01_no = "N/A"
                best_sellers_rank_01_category = "N/A"
                best_sellers_rank_02_no = "N/A"
                best_sellers_rank_02_category = "N/A"

                try:
                    # First structure
                    try:
                        rank_01_element = self.driver.find_element(By.CSS_SELECTOR, "#detailBullets_feature_div+ .detail-bullet-list .a-list-item")
                        if rank_01_element:
                            rank_text_01 = rank_01_element.text
                            match_01_no = re.search(r"#([\d,]+)", rank_text_01)
                            match_01_category = re.search(r"in (.+?) \(", rank_text_01)
                            best_sellers_rank_01_no = match_01_no.group(1).replace(",", "").strip() if match_01_no else "N/A"
                            best_sellers_rank_01_category = match_01_category.group(1).strip() if match_01_category else "N/A"

                        rank_02_element = self.driver.find_element(By.CSS_SELECTOR, "#detailBullets_feature_div+ .detail-bullet-list ul li .a-list-item")
                        if rank_02_element:
                            rank_text_02 = rank_02_element.text
                            match_02_no = re.search(r"#([\d,]+)", rank_text_02)
                            match_02_category = re.search(r"in (.+?)$", rank_text_02)
                            best_sellers_rank_02_no = match_02_no.group(1).replace(",", "").strip() if match_02_no else "N/A"
                            best_sellers_rank_02_category = match_02_category.group(1).strip() if match_02_category else "N/A"
                    except Exception as e:
                        self.logger.info("😞 First structure failed. Trying second structure.")

                    # Second structure
                    try:
                        rank_01_element_alt = self.driver.find_element(By.CSS_SELECTOR, "#productDetails_detailBullets_sections1 tr:nth-child(2) td span span")
                        if rank_01_element_alt:
                            rank_text_01_alt = rank_01_element_alt.text
                            match_01_alt_no = re.search(r"#([\d,]+)", rank_text_01_alt)
                            match_01_alt_category = re.search(r"in (.+?) \(", rank_text_01_alt)
                            best_sellers_rank_01_no = match_01_alt_no.group(1).replace(",", "").strip() if match_01_alt_no else "N/A"
                            best_sellers_rank_01_category = match_01_alt_category.group(1).strip() if match_01_alt_category else "N/A"

                        rank_02_element_alt = self.driver.find_element(By.CSS_SELECTOR, "#productDetails_detailBullets_sections1 tr:nth-child(2) td br + span")
                        if rank_02_element_alt:
                            rank_text_02_alt = rank_02_element_alt.text
                            match_02_alt_no = re.search(r"#([\d,]+)", rank_text_02_alt)
                            match_02_alt_category = re.search(r"in (.+?)$", rank_text_02_alt)
                            best_sellers_rank_02_no = match_02_alt_no.group(1).replace(",", "").strip() if match_02_alt_no else "N/A"
                            best_sellers_rank_02_category = match_02_alt_category.group(1).strip() if match_02_alt_category else "N/A"
                    except Exception as e:
                        self.logger.info("😩 Second structure failed. Trying third structure.")

                    # Third structure
                    try:
                        # Find the table containing Best Sellers Rank
                        rank_table = self.driver.find_element(By.ID, "productDetails_detailBullets_sections1")

                        # Locate the specific row for Best Sellers Rank
                        rank_row = rank_table.find_element(By.XPATH, ".//tr[th[contains(text(),'Best Sellers Rank')]]")

                        # Extract the text content for the first rank
                        rank_spans = rank_row.find_elements(By.XPATH, ".//span/span")
                        if rank_spans and len(rank_spans) > 0:
                            rank_text_01_third = rank_spans[0].text  # Get the first span text
                            match_01_third_no = re.search(r"#([\d,]+)", rank_text_01_third)
                            match_01_third_category = re.search(r"in (.+?) \(", rank_text_01_third)
                            best_sellers_rank_01_no = match_01_third_no.group(1).replace(",", "").strip() if match_01_third_no else "N/A"
                            best_sellers_rank_01_category = match_01_third_category.group(1).strip() if match_01_third_category else "N/A"

                        # Extract the text content for the second rank
                        if rank_spans and len(rank_spans) > 1:
                            rank_text_02_third = rank_spans[1].text  # Get the second span text
                            match_02_third_no = re.search(r"#([\d,]+)", rank_text_02_third)
                            match_02_third_category = re.search(r"in (.+?)$", rank_text_02_third)
                            best_sellers_rank_02_no = match_02_third_no.group(1).replace(",", "").strip() if match_02_third_no else "N/A"
                            best_sellers_rank_02_category = match_02_third_category.group(1).strip() if match_02_third_category else "N/A"
                        else:
                            best_sellers_rank_02_no = "N/A"
                            best_sellers_rank_02_category = "N/A"

                    except Exception as e:
                        self.logger.info("🤬 Third structure failed.")

                    self.logger.info(f"👍 Best Sellers Rank 01: {best_sellers_rank_01_no} in {best_sellers_rank_01_category}")
                    self.logger.info(f"👍 Best Sellers Rank 02: {best_sellers_rank_02_no} in {best_sellers_rank_02_category}")

                except Exception as e:
                    self.logger.error(f"⚠️ Unexpected error extracting best seller rank: {e}")
            except Exception as e:
                self.logger.warning(f"⚠️ Best Sellers Rank section did not load within timeout. Skipping extraction. Error: {e}")
                best_sellers_rank_01_no = "N/A"
                best_sellers_rank_01_category = "N/A"
                best_sellers_rank_02_no = "N/A"
                best_sellers_rank_02_category = "N/A"

            # Extract and format `date_first_available`
            try:
                raw_date = None
                formatted_date = "N/A"

                # List of CSS selectors to try
                selectors = [
                    "#productDetails_detailBullets_sections1 .prodDetAttrValue::text",
                    "#productDetails_detailBullets_sections1 tr+ tr .prodDetAttrValue::text",
                    "#detailBullets_feature_div li:nth-child(1) .a-text-bold+ span::text",
                    "#detailBullets_feature_div li:nth-child(2) .a-text-bold+ span::text",
                    "#detailBullets_feature_div li:nth-child(3) .a-text-bold+ span::text",
                    "#detailBullets_feature_div li:nth-child(4) .a-text-bold+ span::text",
                    "#detailBullets_feature_div li:nth-child(5) .a-text-bold+ span::text",
                    "li:nth-child(1) .a-text-bold+ span::text",
                    "li:nth-child(2) .a-text-bold+ span::text",
                    "li:nth-child(3) .a-text-bold+ span::text",
                    "li:nth-child(4) .a-text-bold+ span::text",
                    "li:nth-child(5) .a-text-bold+ span::text",
                    "li:nth-child(6) .a-text-bold+ span::text",
                    "#detailBulletsWrapper_table tr:contains('Date First Available') td::text",
                    "#productDetails_detailBullets_sections1 tr:contains('Date First Available') td::text",
                ]

                # Loop through selectors and validate the extracted value
                for selector in selectors:
                    raw_date = response.css(selector).get()
                    if raw_date:
                        raw_date = raw_date.strip()
                        self.logger.info(f"Attempting to parse 'date_first_available': {raw_date}")

                        # Check if the raw_date matches common date patterns
                        if re.match(r"^\d{1,2} [A-Za-z]+ \d{4}$", raw_date):  # Example: "10 August 2023"
                            formatted_date = datetime.strptime(raw_date, "%d %B %Y").strftime("%d/%m/%Y")
                            break
                        else:
                            self.logger.info(f"😶 Extracted value is not a date: {raw_date}")
                    else:
                        self.logger.info(f"🤯 No value found with selector: {selector}")

                # If no valid date was found, log a warning
                if formatted_date == "N/A":
                    self.logger.warning("Date First Available not found or could not be parsed.")

            except Exception as e:
                self.logger.error(f"⚠️ Error extracting `date_first_available`: {e}")
                formatted_date = "N/A"

            # Extract #1 Best Seller
            first_best_seller = "YES" if "Best Seller" in response.css(".p13n-best-seller-badge::text").get(default="") else "NO"
            self.logger.info(f"🙊 Extracted #1 Best Seller: {first_best_seller}")

            # Extract Amazon Choice
            amazon_choice = "YES" if "Choice" in response.css("div#acBadge_feature_div span::text").get(default="") else "NO"
            self.logger.info(f"🐵 Extracted Amazon Choice: {amazon_choice}")
            
            # Extract Limited Time Deal
            limited_time_deal = "YES" if response.css("#dealBadgeSupportingText span::text").get() else "NO"
            self.logger.info(f"🙉 Extracted Limited Time Deal: {limited_time_deal}")

            # Extract product weight and dimensions from different structures
            try:
                dimensions = "N/A"
                weight = "N/A"

                # List of CSS selectors to try extracting dimensions and weight
                selectors = [
                    "#productDetails_techSpec_section_1 tr:contains('Package Dimensions') td::text",  # Tech spec section
                    "#productDetails_techSpec_section_1 td.prodDetAttrValue:contains('Package Dimensions')::text",  # Tech spec section
                    "#productDetails_techSpec_section_1 tr:contains('Product Dimensions') td::text",  # Tech spec section
                    "#detailBulletsWrapper_feature_div tr:contains('Package Dimensions') td::text",  # Product details section
                    "#detailBulletsWrapper_feature_div tr:contains('Product Dimensions') td::text",  # Product details section
                    "#detailBullets_feature_div li:contains('Package Dimensions') span::text",  # Bullet list section
                    "#detailBullets_feature_div li:contains('Product Dimensions') span::text",  # Bullet list section
                    "#detailBullets_feature_div li:contains('Dimensions') span::text",  # Bullet list section
                    "#productDetails_detailBullets_sections1 tr:contains('Dimensions') td::text",  # Another product details section
                    "#productDescription span:contains('Net Weight:') + span::text",  # Description section for Net Weight
                    "#productDescription span:contains('Net Weight:')::text",  # Description section for Net Weight
                    "li:nth-child(1) .a-text-bold+ span::text",
                    "li:nth-child(2) .a-text-bold+ span::text",
                    "li:nth-child(3) .a-text-bold+ span::text",
                    "li:nth-child(4) .a-text-bold+ span::text",
                    "li:nth-child(5) .a-text-bold+ span::text",
                    "li:nth-child(6) .a-text-bold+ span::text",
                ]

                # Loop through selectors to find the dimensions and weight
                for selector in selectors:
                    dimensions_weight_row = response.css(selector).get()

                    if dimensions_weight_row:
                        dimensions_weight_row = dimensions_weight_row.strip()

                        # Log the selector being inspected
                        self.logger.info(f"Inspecting selector: {selector}")

                        # Check if the row contains dimensions and weight
                        if "g" in dimensions_weight_row and "cm" in dimensions_weight_row:
                            # Split by semicolon to separate dimensions and weight
                            dimensions_weight_split = dimensions_weight_row.split(';')

                            # Extract weight
                            weight = dimensions_weight_split[1].strip().replace('g', '').replace(' ', '').replace('\u200e', '') if len(dimensions_weight_split) > 1 else "N/A"
                            
                            # Extract dimensions
                            dimensions = dimensions_weight_split[0].strip().replace('cm', '').replace('\u200e', '').replace('x', 'x').strip() if len(dimensions_weight_split) > 0 else "N/A"
                            
                            # Format dimensions as "L x W x H"
                            dimension_parts = dimensions.split(" x ")
                            if len(dimension_parts) == 3:  # If we have exactly 3 parts
                                dimensions = f"{dimension_parts[0]}L x {dimension_parts[1]}W x {dimension_parts[2]}H"

                            # Log the extracted dimensions and weight
                            self.logger.info(f"📏 Extracted Dimensions: {dimensions}")
                            self.logger.info(f"⚖️ Extracted Weight: {weight}")
                            break  # Stop once the data is found

                # If no dimensions and weight are found, log warnings
                if dimensions == "N/A" or weight == "N/A":
                    self.logger.warning("⚠️ No dimensions or weight found.")

            except Exception as e:
                self.logger.error(f"⚠️ Error extracting dimensions and weight: {e}")
                dimensions = "N/A"
                weight = "N/A"

            # Extracting Item Model Number
            model_number = response.css("th:contains('Item model number') + td::text").get(default="N/A").strip()
            # Clean the model number by removing unwanted Unicode characters
            model_number = model_number.strip().replace('\u200e', '') if model_number != "N/A" else "N/A"
            self.logger.info(f"🆔 Extracted Item Model Number: {model_number}")


            # Construct Product Details
            product_details = {
                "serial_number": serial_number,
                "scraping_date": scraping_date,
                "scraping_time": scraping_time,
                "title": product_title,
                "price": price,
                "previous_price": previous_price,
                "discount": discount,
                "currency": currency,
                "product_link": product_link,
                "category_01": str(category_01),
                "category_02": str(category_02),
                "category_03": str(category_03),
                "category_04": str(category_04),
                "category_05": str(category_05),
                "category_06": str(category_06),
                "sold_by": sold_by,
                "sold_by_link": sold_by_link,
                "seller_id": seller_id,
                "sellers": num_sellers,
                "fulfilled_by": fulfilled_by,
                "fulfillment_type": fulfillment_type,
                "brand": brand,
                "bought_in_past_month": bought_in_past_month,
                "image_link": image_link,
                "star_rating": star_rating,
                "total_rating": total_rating,
                "availability": availability,
                "best_sellers_rank_01_no": best_sellers_rank_01_no,
                "best_sellers_rank_01_category": best_sellers_rank_01_category,
                "best_sellers_rank_02_no": best_sellers_rank_02_no,
                "best_sellers_rank_02_category": best_sellers_rank_02_category,
                "date_first_available": formatted_date,
                "#1_best_seller": str(first_best_seller),
                "amazons_choice": str(amazon_choice),
                "limited_time_deal": str(limited_time_deal),
                "weight": weight,
                "dimensions": dimensions,
                "model_number": model_number,
                "ASIN": asin,
            }

            # Log product details for debugging
            self.logger.debug(f"Product details for ASIN {asin}: {json.dumps(product_details, indent=4)}")

            # Save Data
            if product_details not in data_collected:
                data_collected.append(product_details)
                with open(self.output_file, "w", encoding="utf-8") as file:
                    json.dump(data_collected, file, indent=4, ensure_ascii=False)
            self.logger.info(f"📌 Product details saved to {self.output_file}")
        except Exception as e:
            self.logger.error(f"⚠️ Error parsing product details for ASIN {response.meta['asin']}: {e}")

    # Quit the Selenium driver
    def closed(self, reason):
        self.driver.quit()

# Run the spider
if __name__ == "__main__":
    process = CrawlerProcess(settings={"LOG_LEVEL": "INFO"})
    process.crawl(AmazonProductsSpider)
    process.start()

    print(f"🏁 Scraping completed. Data saved to '{AmazonProductsSpider().output_file}'")