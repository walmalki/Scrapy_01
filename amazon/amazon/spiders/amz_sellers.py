import scrapy


class AmzSellersSpider(scrapy.Spider):
    name = "amz_sellers"
    allowed_domains = ["amazon.sa"]
    start_urls = ["https://amazon.sa"]

    def parse(self, response):
        pass
