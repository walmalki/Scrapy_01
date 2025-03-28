import scrapy


class AmzBtsSpider(scrapy.Spider):
    name = "amz_bts"
    allowed_domains = ["amazon.sa"]
    start_urls = ["https://amazon.sa"]

    def parse(self, response):
        pass
