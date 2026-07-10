# 调研结论

## 一句话判断
已整理 3 个相邻方向，最接近的是 davide97l/ecommerce_price_scraper（15/100），目前更适合用来找灵感，不适合直接采用。

## 已整理的线索

### 1. davide97l/ecommerce_price_scraper（相邻参考） · ★ 30 · 更新 2024-02-22
- 关联度：15/100
- 得分原因：公开证据只支持较弱相邻关系；项目定位或使用方式也有差异；该项目可核对线索：Scrape the prices of a set of products from different vendors via e-commerce website

...[truncated]...

all 天猫, and Jingdong 京东.。
- 差异部分：需求：给定一条商品链接；项目实际：使用商品关键词搜索，不接收商品链接；需求：算出最终到手价
- 地址：https://github.com/davide97l/ecommerce_price_scraper

### 2. Dashboard-Design/Nilper_Scraping（相邻参考） · ★ 3 · 更新 2025-08-09
- 关联度：15/100
- 得分原因：公开证据只支持较弱相邻关系；项目定位或使用方式也有差异；该项目可核对线索：A Python web scraping project that collects Nilper chair product data — including na

...[truncated]...

ard for market analysis.。
- 差异部分：需求：给定一条商品链接，读取该链接的价格和优惠明细；项目实际：从固定网站的商品列表页批量抓取所有商品的价格，不接收单个商品链接；需求：算出最终到手价（考虑优惠明细）
- 地址：https://github.com/Dashboard-Design/Nilper_Scraping

### 3. nexscope-ai/eCommerce-Skills（相邻参考） · ★ 342 · 更新 2026-06-10
- 关联度：15/100
- 得分原因：公开证据只支持较弱相邻关系；项目定位或使用方式也有差异；该项目可核对线索：E-commerce skills for AI agents — product research, marketing automation, supply cha

...[truncated]...

Shop, and all platforms.。
- 差异部分：需求：给定一条商品链接，读取价格和优惠明细，算出最终到手价；项目实际提供：AI代理技能（如利润计算器、定价策略），需要用户手动输入数据或依赖AI代理的上下文，不直接处理商品链接
- 地址：https://github.com/nexscope-ai/eCommerce-Skills

## 本次消耗
- LLM Token：输入 32950，输出 5127，合计 38077。