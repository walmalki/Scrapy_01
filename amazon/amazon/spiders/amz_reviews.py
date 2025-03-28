
import scrapy
from scrapy.crawler import CrawlerProcess
from selenium import webdriver
from selenium.webdriver.common.by import By
from scrapy.http import HtmlResponse
from urllib.parse import quote
from datetime import datetime, timedelta
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
import json
import os
import random
import time
import re
import requests
import warnings

# Ignore CryptographyDeprecationWarning
warnings.filterwarnings("ignore", category=DeprecationWarning, module="cryptography")

# Specify ASINs to scrape directly (Prioritized)
specific_asins = []  # If empty, the script will load ASINs from the input file

# Define other configurations
base_url = "https://www.amazon.sa"  # Base URL for reviews
MAX_REVIEWS_PER_ASIN = 20           # Maximum reviews per ASIN
OUTPUT_DIR = "data"                 # Directory to save the output file
INPUT_FILE = "data/amz_asin.jsonl"  # ASINs input file (JSONL format)

# Global variables to hold data
data_collected = []  # Store collected review data

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

# Custom settings for Scrapy Spider
custom_settings = {
    'LOG_LEVEL': 'INFO',
    'CONCURRENT_REQUESTS': 1,
    'DOWNLOAD_DELAY': random.uniform(5, 10),  # Increase delay to reduce throttling
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

# Define the Spider
class AmzReviewsSpider(scrapy.Spider):
    name = "amz_reviews"

    # Define allowed domains
    allowed_domains = ["amazon.sa"]
    # Define custom settings
    custom_settings = custom_settings
    # We'll use self.asin_to_serial to map an ASIN to its input serial number (e.g. "ASN50")
    asin_to_serial = {}
    # Internal counter fallback if no serial is found in the input file.
    serial_number_counter = 1
    # Dictionary to store cumulative review counter per ASIN
    review_counter = {}

    # function to initialize the Spider
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Save credentials
        self.email = config.get("email")
        self.password = config.get("password")
        # Initialize Selenium WebDriver with a rotating user agent
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-extensions")  # Disable extensions
        chrome_options.add_argument("--disable-software-rasterizer")  # Disable software rasterizer
        chrome_options.add_argument("--blink-settings=imagesEnabled=false")  # Disable images
        chrome_options.add_argument(f"user-agent={USER_AGENT}")  # Use the dynamic ScrapeOps User-Agent
        self.driver = webdriver.Chrome(options=chrome_options)
        # Set output file path using _generate_output_filename()
        self.output_file = self._generate_output_filename()
        self.review_counter = {}  # Dictionary to store cumulative review counter per ASIN
        # Initialize the processed_reviews attribute to track processed reviews for each ASIN
        self.processed_reviews = {}  # Initialize processed_reviews here
        # Build a mapping from ASIN to its serial number from the input file.
        self._load_asin_to_serial_map()

    # Load the mapping from ASIN to the serial number from the input file
    def _load_asin_to_serial_map(self):
        """Load the mapping from ASIN to the serial number from the input file."""
        try:
            with open(INPUT_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    record = json.loads(line)
                    asin = record.get("asin")
                    serial = record.get("serial_number", "")
                    if asin:
                        self.asin_to_serial[asin] = serial  # e.g. "ASN50"
        except Exception as e:
            self.logger.error(f"Error loading ASIN to serial mapping: {e}")

    # Function to prompt the user to filter by serial range or resume from the last stop
    def _load_asins_with_serial_filter_or_prompt(self):
        """
        Load ASIN records (as dictionaries) from the input file.
        Each record will have:
          - 'asin'
          - 'AP_serial_number' (converted from input serial number, e.g. "ASN50" -> "AP50")
        Allows filtering by serial range or resuming from the last stop.
        """
        try:
            records = []
            serial_numbers = []
            with open(INPUT_FILE, "r", encoding="utf-8") as file:
                for line in file:
                    record = json.loads(line)
                    serial_numbers.append(record.get("serial_number", ""))
                    ap_serial = record.get("serial_number", "").replace("ASN", "AP")
                    records.append({"asin": record["asin"], "AP_serial_number": ap_serial})

            use_range = input("Do you want to filter by serial number range? (yes/no): ").strip().lower()
            if use_range == "yes":
                start_sn = input("Enter start serial number (e.g., ASN50): ").strip()
                end_sn = input("Enter end serial number (e.g., ASN100): ").strip()
                if start_sn in serial_numbers and end_sn in serial_numbers:
                    start_idx = serial_numbers.index(start_sn)
                    end_idx = serial_numbers.index(end_sn)
                    records = records[start_idx:end_idx + 1]
                    self.logger.info(f"🌚 Filtered ASINs from serial {start_sn} to {end_sn}")
                else:
                    self.logger.warning("⚠️ Invalid serial number range provided. Using all ASINs.")
            else:
                choice = input("Do you want to continue from the last stop? (yes/no): ").strip().lower()
                if choice == "yes":
                    last_serial_number = self._get_last_serial_number(log_message=False)
                    if last_serial_number > 0:
                        last_serial_formatted = f"ASN{last_serial_number}"
                        if last_serial_formatted in serial_numbers:
                            last_serial_index = serial_numbers.index(last_serial_formatted)
                            records = records[last_serial_index + 1:]
                            self.logger.info(f"🚣 Resuming from serial number {last_serial_formatted}")
                        else:
                            self.logger.warning(f"☢️ Could not find {last_serial_formatted} in the input file. Starting from beginning.")
            if not records:
                self.logger.error("⚠️ No ASINs found to scrape. Exiting...")
                exit(1)
            return records
        except (FileNotFoundError, json.JSONDecodeError) as e:
            self.logger.error(f"⚠️ Error loading ASINs: {e}")
            exit(1)

    # Function to generate a unique output filename
    def _generate_output_filename(self):
        """Generate a unique output filename based on the current GMT+3 date and time."""
        now = datetime.utcnow() + timedelta(hours=3)
        timestamp = now.strftime("%d-%m-%Y_%I-%M-%p")
        return os.path.join(OUTPUT_DIR, f"amz_reviews_{timestamp}.json")

    # Function to find the highest product serial number
    def _get_last_serial_number(self, log_message=True):
        """
        Find the highest product serial number (the numeric part from the output file).
        Assumes that in the output file each product has a key 'AP_serial_number' like 'AP50'.
        Uses a regular expression to extract the numeric portion of the serial number.
        """
        try:
            # List all files in the OUTPUT_DIR that match the naming pattern of the output files
            files = [f for f in os.listdir(OUTPUT_DIR) if f.startswith("amz_reviews_") and f.endswith(".json")]
            
            if not files:
                # No previous output files found
                self.logger.info("⚠️ No previous output files found.")
                return 0
            
            # Sort the files by creation time (most recent first)
            files.sort(key=lambda f: os.path.getctime(os.path.join(OUTPUT_DIR, f)), reverse=True)
            latest_file = os.path.join(OUTPUT_DIR, files[0])
            
            with open(latest_file, "r", encoding="utf-8") as file:
                existing_data = json.load(file)
                
                if isinstance(existing_data, list) and len(existing_data) > 0:
                    # Use regular expression to extract the numeric part of the 'AP_serial_number'
                    last_serial = max(
                        int(re.search(r"\d+", item["AP_serial_number"]).group()) 
                        for item in existing_data if "AP_serial_number" in item
                    )
                    if log_message:
                        self.logger.info(f"🌗 Last product serial number found: AP{last_serial}")
                    return last_serial
                else:
                    if log_message:
                        self.logger.warning("⚠️ No valid serial numbers found in the existing data.")
                    return 0
        except Exception as e:
            self.logger.error(f"⚠️ Error reading last serial number: {e}")
        
        return 0

    # Function to handle Amazon login
    def login(self, max_retries=3):
        """Handles Amazon login process using Selenium with retry attempts."""
        attempt = 0
        while attempt < max_retries:
            try:
                self.logger.info("🔑 Attempting to log in to Amazon...")

                login_url = (
                    "https://www.amazon.sa/-/en/ap/signin?"
                    "openid.pape.max_auth_age=0&openid.return_to=https%3A%2F%2Fwww.amazon.sa%2F%3Fref_%3Dnav_signin&"
                    "openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select&"
                    "openid.assoc_handle=saflex&openid.mode=checkid_setup&"
                    "openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select&"
                    "openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0"
                )
                self.driver.get(login_url)
                self.logger.info(f"Navigating to login URL: {login_url}")
                time.sleep(2)  # Allow page to load
                WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, 'html')))

                # Wait for the email field
                WebDriverWait(self.driver, 20).until(EC.visibility_of_element_located((By.ID, "ap_email")))
                self.logger.info("🔑 Interacting with email field")

                # Handle CAPTCHA or MFA challenge
                if "ap/cvf" in self.driver.current_url or "captcha" in self.driver.current_url:
                    self.logger.warning("⚠️ CAPTCHA or MFA triggered. Manual intervention required.")
                    self.driver.save_screenshot("mfa_or_captcha.png")
                    self.driver.quit()  # Quit driver to avoid further attempts
                    raise Exception("MFA or CAPTCHA required, stopping execution.")

                # Enter email
                email_input = self.driver.find_element(By.ID, "ap_email")
                email_input.send_keys(self.email)
                self.driver.find_element(By.ID, "continue").click()

                # Wait for the password field
                WebDriverWait(self.driver, 20).until(EC.visibility_of_element_located((By.ID, "ap_password")))
                self.logger.info("🔑 Interacting with password field")
                time.sleep(1)
                password_input = self.driver.find_element(By.ID, "ap_password")
                password_input.send_keys(self.password)
                self.driver.find_element(By.ID, "signInSubmit").click()

                time.sleep(5)  # Allow time for login processing
                self.session_cookies = self.driver.get_cookies()  # Save session cookies
                self.logger.info("🔑 Successfully logged in to Amazon! Session cookies saved.")
                return True

            except Exception as e:
                attempt += 1
                self.logger.error(f"Error logging in (attempt {attempt}/{max_retries}): {e}")
                if attempt >= max_retries:
                    self.logger.error("❌ Maximum login attempts reached. Stopping scraper.")
                    self.driver.quit()  # Close the driver on failure
                    raise e  # Re-raise the exception to stop the scraper
                else:
                    self.logger.warning(f"⚠️ Retrying login attempt {attempt}/{max_retries}...")
                    time.sleep(5)  # Retry delay before attempting again

    # Function to start the scraper
    def start_requests(self):
        """Start scraping reviews for the specified ASINs immediately after generating the URL."""
        if specific_asins:
            self.logger.info(">>> Using specific ASINs provided. Skipping prompts.")
            # Load records (asin and its corresponding AP_serial_number) from the input file for the specific ASINs
            records = []
            with open(INPUT_FILE, "r", encoding="utf-8") as file:
                for line in file:
                    rec = json.loads(line)
                    if rec.get("asin") in specific_asins:
                        records.append({
                            "asin": rec["asin"],
                            "AP_serial_number": rec.get("serial_number", "").replace("ASN", "AP")
                        })
            if not records:
                self.logger.error("❌ No matching ASINs found in the input file.")
                return
            asins_to_scrape = records
        else:
            self.logger.info("No specific ASINs provided. Proceeding with prompt-based loading.")
            asins_to_scrape = self._load_asins_with_serial_filter_or_prompt()

        # Ensure successful login before proceeding
        if not self.login():
            self.logger.error("❌ Login failed. Stopping scraper.")
            raise Exception("Login failed. Stopping the scraper.")

        self.logger.info("🔄 Adding session cookies to Scrapy requests...")

        for idx, record in enumerate(asins_to_scrape):
            asin = record["asin"]
            AP_serial_number = record["AP_serial_number"]

            # Initialize review counter for each ASIN
            if asin not in self.review_counter:
                self.review_counter[asin] = 1  # Start from review number 1 for each ASIN

            # Prepare meta data and scrape
            asin_encoded = quote(asin, safe='')
            url = f"{base_url}/-/en/product-reviews/{asin_encoded}/ref=cm_cr_dp_d_show_all_btm?ie=UTF8&reviewerType=all_reviews"
            self.logger.info(f"🎲 Generated URL: {url} for ASIN {asin}")

            meta = {
                'cookies': self.session_cookies,
                'asin': asin,
                'AP_serial_number': AP_serial_number,
                'reviews_link': url,
                'page_source': self.driver.page_source,
                'reviews': [],  # Placeholder for reviews
                'proxy': f"http://scrapeops:{custom_settings['SCRAPEOPS_API_KEY']}@proxy.scrapeops.io:5353"  # Proxy for each request
            }

            self.logger.info(f"📌 Navigating to review page for ASIN {asin}")

            yield scrapy.Request(
                url,
                callback=self.parse_reviews,
                headers=FAKE_HEADERS,
                meta=meta,
                dont_filter=True
            )

    # Function to parse reviews
    def parse_reviews(self, response):
        """Parse reviews using Selenium's extracted page source instead of Scrapy requests."""
        meta = response.meta
        asin = meta['asin']
        AP_serial_number = meta['AP_serial_number']
        review_link = meta.get('reviews_link') or response.url

        self.logger.info(f"🌍 Visiting review page for ASIN {asin} - {review_link}")

        # Check for 404 errors
        if response.status == 404:
            self.logger.error(f"⚠️ 404 Error: Page not found for URL {response.url}")
            # You can optionally save the error or handle it further here
            self.save_to_output_file(asin, {
                "ASIN": asin,
                "AP_serial_number": AP_serial_number,
                "reviews": [],  # Empty list when the page is not found
                "reviews_link": review_link
            })
            return  # Exit function if 404 error is encountered

        # Set cookies
        for cookie in self.session_cookies:
            self.driver.add_cookie(cookie)

        # Handle timeouts and retries
        self._handle_timeout(asin)

        retries = 3
        while retries > 0:
            try:
                self.driver.get(review_link)
                WebDriverWait(self.driver, 20).until(
                    EC.presence_of_element_located((By.ID, 'cm_cr-review_list'))
                )
                break
            except TimeoutException:
                self.logger.warning(f"⚠️ Timeout while loading reviews for ASIN {asin} - {AP_serial_number}. Retrying... ({retries} left)")
                retries -= 1
                time.sleep(5)
                if retries == 0:
                    self.logger.error(f"❌ Timeout exceeded for ASIN {asin} - {AP_serial_number}. Saving empty reviews.")
                    self.save_to_output_file(asin, {
                        "ASIN": asin,
                        "AP_serial_number": AP_serial_number,
                        "reviews": [],
                        "reviews_link": review_link
                    })
                    return

        # Scroll to the bottom to load additional content
        time.sleep(5)
        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(8)

        page_source = self.driver.page_source
        response = HtmlResponse(url=review_link, body=page_source, encoding='utf-8')

        self.logger.info(f"📌 Parsing reviews for ASIN {asin} - {AP_serial_number}")

        reviews_section = response.css('div#cm_cr-review_list .review')
        self.logger.info(f"👁️ Found {len(reviews_section)} reviews for ASIN {asin} - {AP_serial_number}")

        if not reviews_section:
            self.logger.warning(f"⚠️ No reviews section found for ASIN {asin}- {AP_serial_number}. Saving empty reviews list.")
            self.save_to_output_file(asin, {
                "ASIN": asin,
                "AP_serial_number": AP_serial_number,
                "reviews": [],
                "reviews_link": review_link
            })
            return

        reviews = self._extract_reviews(response, asin)

        if not reviews:
            self.logger.warning(f"⚠️ No reviews found for ASIN {asin} - {AP_serial_number}. Saving empty reviews list.")
            reviews = []

        item = {
            "ASIN": asin,
            "AP_serial_number": AP_serial_number,
            "reviews": reviews,
            "reviews_link": review_link
        }

        self.logger.info(f"###> ASIN: {asin}.")
        self.logger.info(f"###> AP Serial Number: {AP_serial_number}.")

        # Check if we have reached the max reviews per ASIN
        if self.review_counter.get(asin, 0) > MAX_REVIEWS_PER_ASIN:
            self.logger.warning(f"⚠️ Reached MAX_REVIEWS_PER_ASIN for ASIN {asin} - {AP_serial_number} ({MAX_REVIEWS_PER_ASIN} reviews). Skipping further reviews and pagination.")

        # Log the total reviews for the ASIN (total reviews so far)
        self.logger.info(f"💾 Saving data for ASIN {asin} with {self.review_counter[asin] - 1} reviews.")

        # Save the reviews to the output file
        self.save_to_output_file(asin, item)

        # Handle pagination if there are more pages and we haven't reached the max review limit
        if self.review_counter.get(asin, 0) < MAX_REVIEWS_PER_ASIN:
            yield from self._handle_pagination(response, asin, AP_serial_number)

    # Function to extract reviews
    def _extract_reviews(self, response, asin):
        """Extract reviews from the response and return a list of structured review data."""
        reviews = []
        review_blocks = response.css('div#cm_cr-review_list .review')

        if not review_blocks:
            self.logger.warning(f"⚠️ No reviews found for ASIN {asin}.")
            return []

        # Retrieve the current count of reviews for this ASIN from the cumulative counter
        current_review_count = self.review_counter.get(asin, 0)

        for idx, block in enumerate(review_blocks, start=current_review_count):
            # Check if we have reached the MAX_REVIEWS_PER_ASIN limit
            if idx > MAX_REVIEWS_PER_ASIN:
                break  # Stop processing reviews once we reach the limit

            # Extract customer ID
            customer_id_raw = block.css('a.a-profile::attr(href)').get()
            customer_id = "N/A"
            if customer_id_raw and "amzn1.account." in customer_id_raw:
                parts = customer_id_raw.split('amzn1.account.')
                if len(parts) > 1:
                    customer_id = parts[1].split('/')[0]

            # Extract review date and reviewer location from the review text
            review_date_raw = block.css('.review-date::text').get()
            review_date = None
            reviewer_location = None
            if review_date_raw:
                # Extract date
                date_match = re.search(r'(\d{1,2} \w+ \d{4})', review_date_raw)
                if date_match:
                    review_date = datetime.strptime(date_match.group(1), '%d %B %Y').strftime('%d/%m/%Y')
                # Extract location if available
                location_match = re.search(r'Reviewed in ([A-Za-z\s]+) on', review_date_raw)
                if location_match:
                    reviewer_location = location_match.group(1).strip()
                    if reviewer_location.lower().startswith('the '):
                        reviewer_location = reviewer_location[4:].strip()

            # Extract review title text, ensuring it captures the text even if the title might be nested or empty.
            review_title_element = block.css('.review-title span::text').getall()
            review_title = ''.join(review_title_element).strip() if review_title_element else None

            # Extract full review text
            review_text_elements = block.css('.review-text-content span::text').getall()
            review_text = ' '.join(review_text_elements).strip() if review_text_elements else None

            # Ensure title does not contain the rating text
            if review_title and "out of 5 stars" in review_title:
                review_title = review_title.split("out of 5 stars")[-1].strip()

            # Extract review ID
            review_id_raw = block.css('#cm_cr-review_list li[data-hook="review"]::attr(id)').get() or block.xpath('@id').get()
            review_id = None
            if review_id_raw:
                review_id = re.sub(r'^customer_review(_foreign)?-', '', review_id_raw)
            else:
                review_id = f"manual_{random.randint(1000, 9999)}"

            # Skip duplicate check
            if review_id and review_id in self.processed_reviews.get(asin, set()):
                self.logger.info(f"⚠️ Skipping duplicate review {review_id} for ASIN {asin}.")
                continue
            elif not review_id:
                self.logger.warning(f"⚠️ Review ID missing for ASIN {asin}. Assigning fallback ID.")
                review_id = f"manual_{random.randint(1000, 9999)}"

            # Extract review details
            scraping_date = datetime.utcnow().strftime("%d/%m/%Y")
            scraping_time = (datetime.utcnow() + timedelta(hours=3)).strftime("%I:%M %p")
            customer_name = block.css('.a-profile-name::text').get()
            review_rating_raw = block.css('.review-rating span.a-icon-alt::text').get()
            review_rating = re.search(r'(\d)', review_rating_raw).group(1) if review_rating_raw else None
            verified_purchase_text = block.css('.a-color-state::text').get()
            verified_purchase = "YES" if verified_purchase_text and "Verified Purchase" in verified_purchase_text else "NO"
            review_votes = block.css('.cr-vote-text::text').get() or "0"

            # Add to processed reviews set
            self.processed_reviews.setdefault(asin, set()).add(review_id)

            # Generate a unique serial number
            review_serial_number = f"RV{idx}"

            self.logger.info(f"🆔 Review Serial Number for ASIN {asin}: {review_serial_number}")
            self.logger.info(f"📅 Current Scraping Date RV{idx}: {scraping_date}")
            self.logger.info(f"🕰️ Current Scraping Time RV{idx}: {scraping_time}")
            self.logger.info(f"📝 Review {idx}: Customer Name: {customer_name}")
            self.logger.info(f"📝 Review {idx}: Customer ID: {customer_id}")
            self.logger.info(f"📝 Review {idx}: Customer Reviewer Location: {reviewer_location}")
            self.logger.info(f"📝 Review {idx}: Review Title: {review_title}")
            self.logger.info(f"📝 Review {idx}: Review Text: {review_text}")
            self.logger.info(f"📝 Review {idx}: Review Rating: {review_rating}")
            self.logger.info(f"📝 Review {idx}: Review Date: {review_date}")
            self.logger.info(f"📝 Review {idx}: Review ID: {review_id}")
            self.logger.info(f"📝 Review {idx}: Review Votes: {review_votes}")
            self.logger.info(f"📝 Review {idx}: Review Verified Purchase: {verified_purchase}")
            self.logger.info(f"📝 Review {idx}: Reviews Link: {self.driver.current_url}")

            # Construct the review dictionary
            review_data = {
                "review_serial_number": review_serial_number,
                "scraping_date": scraping_date,
                "scraping_time": scraping_time,
                "customer_name": customer_name,
                "customer_id": customer_id,
                "reviewer_location": reviewer_location,
                "review_title": review_title,
                "review_text": review_text,
                "review_rating": review_rating,
                "review_date": review_date,
                "review_id": review_id,
                "helpful_votes": review_votes,
                "Verified_Purchase": verified_purchase,
                "reviews_link": self.driver.current_url
            }

            self.logger.info(f"✅ Extracted review {review_serial_number} for ASIN {asin}")

            # Construct and append the review data...
            reviews.append(review_data)

        # Update the cumulative review counter for the next page
        self.review_counter[asin] = current_review_count + len(reviews)

        return reviews

    # Function to handle timeouts
    def _handle_timeout(self, asin):
        retries = 3
        delay = 5  # Initial delay
        while retries > 0:
            try:
                # Retry code here, for example, to reload the page or handle the timeout
                # If successful, exit the loop
                break
            except TimeoutException:
                retries -= 1
                self.logger.warning(f"⚠️ Timeout for ASIN {asin}. Retrying in {delay} seconds...")
                time.sleep(delay)
                delay += 5  # Incremental delay for each retry
                if retries == 0:
                    self.logger.error(f"❌ Timeout exceeded for ASIN {asin}")

    # Function to handle pagination
    def _handle_pagination(self, response, asin, AP_serial_number):
        """Handles pagination and ensures additional pages are scraped."""
        try:
            self.logger.info(f"🔄 Checking for pagination on ASIN {asin}")

            next_page_btn = None

            # List of possible CSS selectors for the pagination button
            possible_selectors = [
                "li.a-last a", 
                "span li.a-last a",
                "ul.a-pagination li.a-last a", 
                "span.a-declarative li.a-last a", 
                "div.a-text-center a",
                "li.a-disabled a"
            ]

            # Loop through possible selectors to find the next page button
            for selector in possible_selectors:
                try:
                    next_page_btn = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if next_page_btn.is_displayed() and next_page_btn.is_enabled():
                        self.logger.info(f"😬 Next page button found using {selector}")
                        break
                except Exception as e:
                    self.logger.warning(f"⚠️ Could not find next page button using {selector}.")
                    continue

            # If the next page button is found
            if next_page_btn:
                next_page_url = next_page_btn.get_attribute("href")

                # Check if the URL is valid and contains "product-reviews" and ASIN
                if next_page_url and "product-reviews" in next_page_url and asin in next_page_url:
                    self.logger.info(f"📌 Clicking next page for ASIN {asin}: {next_page_url}")

                    # Scroll to the next page button and click
                    self.driver.execute_script("arguments[0].scrollIntoView();", next_page_btn)
                    time.sleep(2)
                    next_page_btn.click()
                    time.sleep(3)

                    # Wait for the page to load after clicking the next page
                    WebDriverWait(self.driver, 15).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "#cm_cr-review_list .celwidget"))
                    )

                    # Prepare new meta data for Scrapy Request
                    new_meta = {
                        'asin': asin,
                        'AP_serial_number': AP_serial_number,
                        'reviews_link': next_page_url,
                        'page_source': self.driver.page_source,
                        'proxy': f"http://scrapeops:{custom_settings['SCRAPEOPS_API_KEY']}@proxy.scrapeops.io:5353"  # Proxy for each request
                    }

                    # Log that a Scrapy request is being sent for the next page
                    self.logger.info(f"📩 Sending Scrapy request for next page: {next_page_url}")

                    # Yield the Scrapy request to continue scraping the next page
                    yield scrapy.Request(
                        url=next_page_url,
                        callback=self.parse_reviews,  # Your existing review parsing method
                        headers=FAKE_HEADERS,  # ✅ Apply Fake Headers
                        meta=new_meta,
                        dont_filter=True
                    )
                else:
                    self.logger.warning(f"⚠️ Skipping next page. URL invalid: {next_page_url}")
            else:
                self.logger.info(f"✅ No more pages found for ASIN {asin}. Finished scraping.")

        except Exception as e:
            self.logger.warning(f"⚠️ Pagination error: {e}")

    # Function to check if the ASIN exists in the output file
    def _asin_exists_in_output(self, asin, new_reviews):
        """
        Check if the ASIN exists in the output file. 
        For our purposes, we want to update the file even if new_reviews is an empty list.
        """
        try:
            if not os.path.exists(self.output_file):
                return False

            with open(self.output_file, "r", encoding="utf-8") as file:
                try:
                    existing_data = json.load(file)
                    if not isinstance(existing_data, list):
                        existing_data = []
                except json.JSONDecodeError:
                    existing_data = []

            for record in existing_data:
                if record.get("ASIN") == asin:
                    # Instead of skipping if duplicate reviews are found,
                    # we want to always update the record.
                    return True
            return False
        except Exception as e:
            self.logger.error(f"⚠️ Error checking ASIN in output: {e}")
            return False

    # Function to save review data safely
    def save_to_output_file(self, asin, item):
        """Save review data safely by using a temporary file before replacing the actual file."""
        try:
            if not os.path.exists(OUTPUT_DIR):
                os.makedirs(OUTPUT_DIR)

            # Generate the output file name after processing each ASIN
            temp_file = self.output_file + ".tmp"

            existing_data = []
            if os.path.exists(self.output_file):
                with open(self.output_file, "r", encoding="utf-8") as file:
                    try:
                        existing_data = json.load(file)
                        if not isinstance(existing_data, list):
                            existing_data = []
                    except json.JSONDecodeError:
                        existing_data = []

            found = False
            for record in existing_data:
                if record.get("ASIN") == asin:
                    found = True
                    existing_reviews = record.get("reviews", [])
                    new_reviews = item.get("reviews", [])
                    merged_reviews = existing_reviews + new_reviews
                    record["reviews"] = merged_reviews
                    break

            if not found:
                existing_data.append(item)

            with open(temp_file, "w", encoding="utf-8") as file:
                json.dump(existing_data, file, indent=4, ensure_ascii=False)

            os.replace(temp_file, self.output_file)

            self.logger.info(f"📌 Successfully saved reviews for ASIN {asin} to {self.output_file}")
        except Exception as e:
            self.logger.error(f"⚠️ Error saving data for ASIN {asin}: {e}")

    # Function to close the Selenium driver
    def closed(self, reason):
        """Quit the Selenium driver after scraping is finished."""
        self.driver.quit()

# Run the spider
if __name__ == "__main__":
    process = CrawlerProcess(settings={"LOG_LEVEL": "INFO"})
    process.crawl(AmzReviewsSpider)
    process.start()
    print(f"🏁 Scraping completed. Data saved to '{AmzReviewsSpider()._generate_output_filename()}'")
    
    if os.path.exists(AmzReviewsSpider()._generate_output_filename()):
        print(f"✅ Data successfully saved to {AmzReviewsSpider()._generate_output_filename()}")
    else:
        print("⚠️ No output file was created. Check logs for issues.")