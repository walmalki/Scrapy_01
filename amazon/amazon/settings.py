# Scrapy settings for amazon project
#
# For simplicity, this file contains only settings considered important or
# commonly used. You can find more settings consulting the documentation:
#
#     https://docs.scrapy.org/en/latest/topics/settings.html
#     https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
#     https://docs.scrapy.org/en/latest/topics/spider-middleware.html


BOT_NAME = 'amazon'

SPIDER_MODULES = ['amazon.spiders']
NEWSPIDER_MODULE = 'amazon.spiders'

# Obey robots.txt rules
ROBOTSTXT_OBEY = False

SCRAPEOPS_API_KEY = 'YOUR_API_KEY'

SCRAPEOPS_PROXY_ENABLED = True
# SCRAPEOPS_PROXY_SETTINGS = {'country': 'sa'}

SCRAPEOPS_FAKE_USER_AGENT_ENABLED = True

SCRAPEOPS_FAKE_HEADERS_ENABLED = True

# Add In The ScrapeOps Monitoring Extension
EXTENSIONS = {
'scrapeops_scrapy.extension.ScrapeOpsMonitor': 500, 
}

LOG_LEVEL = 'INFO'

DOWNLOADER_MIDDLEWARES = {

    ## ScrapeOps Monitor
    'scrapeops_scrapy.middleware.retry.RetryMiddleware': 550,
    'scrapy.downloadermiddlewares.retry.RetryMiddleware': None,

    ## ScrapeOps Fake User Agent
    # 'amz_reviews.middlewares.ScrapeOpsFakeUserAgentMiddleware': 400,
    # 'amz_reviews.middlewares.ScrapeOpsFakeBrowserHeadersMiddleware': 400,
    # 'amz_reviews.middlewares.ScrapeOpsProxyMiddleware': 725,
    'scrapy.downloadermiddlewares.httpproxy.HttpProxyMiddleware': 1,
    
    ## Proxy Middleware
    'scrapeops_scrapy_proxy_sdk.scrapeops_scrapy_proxy_sdk.ScrapeOpsScrapyProxySdk': 725,
}

RETRY_ENABLED = True
RETRY_TIMES = 5  # Increase the retry count to 5
RETRY_HTTP_CODES = [500, 502, 503, 504, 522, 524]  # Retry on these HTTP codes
DOWNLOAD_DELAY = 5  # Add a 5-second delay between downloads

# Max Concurrency On ScrapeOps Proxy Free Plan is 1 thread
CONCURRENT_REQUESTS = 1


# Set settings whose default value is deprecated to a future-proof value
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
FEED_EXPORT_ENCODING = "utf-8"


