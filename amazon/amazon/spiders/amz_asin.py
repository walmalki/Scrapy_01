import scrapy
from scrapy.crawler import CrawlerProcess
import random
import requests
import json
import os
from datetime import datetime

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

class AmzAsinSpider(scrapy.Spider):
    name = "amz_asin"

    # Custom settings for the spider
    custom_settings = {
        'FEEDS': {
            'data/amz_asin.jsonl': {
                'format': 'jsonlines',
                'overwrite': False,  # Append new data to existing file
            }
        },
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

    # Define keywords, pagination limit, and product limit
    keywords = [
        "Pet supply", "Kitchen & Dining", "Home & Kitchen",
        "Health & Household", "Grocery & Gourmet Food", "Beauty & Personal care",
        "Baby products"
    ]
    page_limit = 2  # Maximum number of pages to scrape per keyword
    max_products = 10000  # Maximum total number of ASINs to scrape

    # Persistent storage
    seen_asins = set()  # Stores ASINs already added in this run
    previously_scraped_asins = set()  # ASINs already present in the output file
    last_serial_number = 0  # Tracks the last serial number used
    scraped_count = 0  # Number of ASINs scraped
    new_data_count = 0  # Number of new ASINs added
    skipped_count = 0  # Number of ASINs skipped due to duplication

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.output_file = "data/amz_asin.jsonl"
        self._load_previous_data()

    def _load_previous_data(self):
        """Loads previously scraped ASINs from the output file."""
        if os.path.exists(self.output_file):
            with open(self.output_file, "r", encoding="utf-8") as file:
                for line in file:
                    try:
                        record = json.loads(line)
                        self.previously_scraped_asins.add(record["asin"])
                        serial = int(record["serial_number"].replace("ASN", ""))
                        self.last_serial_number = max(self.last_serial_number, serial)
                    except (ValueError, KeyError):
                        self.logger.warning("Skipping invalid line in JSONLines file.")

    def start_requests(self):
        base_url = "https://www.amazon.sa/s?k="
        for keyword in self.keywords:
            for page in range(1, self.page_limit + 1):
                url = f"{base_url}{keyword}&page={page}"
                yield scrapy.Request(url, callback=self.parse, meta={'keyword': keyword, 'page': page})

    def parse(self, response):
        keyword = response.meta['keyword']
        page = response.meta['page']

        # Extract ASINs from product containers
        product_containers = response.xpath('//div[@data-asin and @data-component-type="s-search-result"]')
        asins = [container.xpath('@data-asin').get() for container in product_containers if container.xpath('@data-asin').get()]

        # Logging results
        if not asins:
            self.logger.warning(f"No products found for keyword '{keyword}' on page {page}.")
        else:
            self.logger.info(f"Scraped {len(asins)} ASINs for keyword '{keyword}' on page {page}.")

        # Current timestamp
        scrape_date = datetime.now().strftime("%Y-%m-%d")
        scrape_time = datetime.now().strftime("%H:%M:%S")

        for asin in asins:
            if self.scraped_count >= self.max_products:
                self.logger.info(f"Reached the product limit of {self.max_products}. Stopping crawl.")
                return  # Stop further scraping

            if asin not in self.seen_asins and asin not in self.previously_scraped_asins:
                self.last_serial_number += 1
                self.scraped_count += 1
                self.new_data_count += 1
                self.seen_asins.add(asin)

                # Save new ASIN incrementally
                with open(self.output_file, "a", encoding="utf-8") as file:
                    record = {
                        'serial_number': f"ASN{self.last_serial_number}",
                        'scrape_date': scrape_date,
                        'scrape_time': scrape_time,
                        'keyword': keyword,
                        'page': page,
                        'asin': asin,
                        're_scrape': True  # Default to True
                    }
                    file.write(json.dumps(record) + "\n")
                    self.logger.info(f"New ASIN added: {asin} (Serial Number: ASN{self.last_serial_number})")
            else:
                self.skipped_count += 1
                self.logger.info(f"Skipping duplicate ASIN: {asin}")

    def closed(self, reason):
        """Log summary of the scrape process."""
        self.logger.info("\n## Scrape Summary:")
        self.logger.info(f"## Total ASINs in file before run: {len(self.previously_scraped_asins)}")
        self.logger.info(f"## New ASINs added: {self.new_data_count}")
        self.logger.info(f"## Total ASINs processed: {self.scraped_count}")
        self.logger.info(f"## ASINs skipped due to duplication: {self.skipped_count}")


# Run the spider
if __name__ == "__main__":
    process = CrawlerProcess()
    process.crawl(AmzAsinSpider)
    process.start()
