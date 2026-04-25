#!/usr/bin/env python3
"""
Test script: Sử dụng crawl4ai để crawl VSD (Vietnamese Securities Depository)
URL: https://www.vsd.vn/vi/tin-thi-truong-co-so

Mục tiêu:
1. Crawl trang danh sách (AJAX POST) → lấy danh sách URL
2. Crawl bài viết chi tiết → extract thông tin structured
3. So sánh kết quả với fetch_vsd.py

Chạy: pip install crawl4ai && crawl4ai-setup && python test_crawl4ai_vsd.py
"""

import asyncio
import json
import re
import sys
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', stream=sys.stderr)
logger = logging.getLogger(__name__)


# ============================================================================
# STEP 1: Crawl trang danh sách tin tức VSD (AJAX POST)
#
# VSD sử dụng AJAX POST để load danh sách tin:
# - GET /vi/tin-thi-truong-co-so → chỉ trả về shell page (menu, navbar)
# - POST /vi/tin-thi-truong-co-so (với __VPToken + SearchKey + CurrentPage)
#   → trả về HTML danh sách tin <li>
#
# Với crawl4ai, ta cần:
# 1. Load trang GET trước để browser render + lấy VPToken
# 2. Dùng js_code để trigger AJAX POST và inject HTML vào DOM
# 3. Extract danh sách link từ kết quả
# ============================================================================
async def test_list_page():
    """Crawl trang tin tức VSD với session-based AJAX."""
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

    logger.info("=" * 70)
    logger.info("STEP 1: Crawl danh sách tin tức VSD (AJAX POST)")
    logger.info("=" * 70)

    browser_config = BrowserConfig(
        headless=True,
        java_script_enabled=True,
    )

    session_id = "vsd_session"
    url = "https://www.vsd.vn/vi/tin-thi-truong-co-so"

    # JS code để trigger AJAX POST lấy danh sách tin
    # Bước 1: Lấy VPToken từ meta tag
    # Bước 2: POST với SearchKey=TCPH, CurrentPage=1
    # Bước 3: Inject kết quả vào DOM
    js_ajax_post = """
    (async () => {
        // 1. Lấy VPToken
        const metaTag = document.querySelector('meta[name="__VPToken"]');
        const vpToken = metaTag ? metaTag.getAttribute('content') : '';
        console.log('VPToken:', vpToken ? vpToken.substring(0, 20) + '...' : 'NOT FOUND');

        // 2. POST request
        const response = await fetch('/vi/tin-thi-truong-co-so', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json;charset=utf-8',
                'X-Requested-With': 'XMLHttpRequest',
                '__VPToken': vpToken
            },
            body: JSON.stringify({SearchKey: 'TCPH', CurrentPage: 1})
        });

        const html = await response.text();
        console.log('AJAX response length:', html.length);

        // 3. Inject vào DOM để crawl4ai extract được
        let container = document.getElementById('vsd-ajax-result');
        if (!container) {
            container = document.createElement('div');
            container.id = 'vsd-ajax-result';
            document.body.appendChild(container);
        }
        container.innerHTML = html;

        // Signal done
        window.__vsdAjaxDone = true;
        console.log('AJAX result injected into DOM');
    })();
    """

    # Wait condition: đợi cho AJAX result được inject
    wait_js = """
    () => {
        return window.__vsdAjaxDone === true;
    }
    """

    news_items = []

    async with AsyncWebCrawler(config=browser_config) as crawler:
        # Bước 1: Load trang ban đầu
        logger.info(f"📄 Loading initial page: {url}")
        initial_config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            page_timeout=30000,
            session_id=session_id,
        )
        result = await crawler.arun(url=url, config=initial_config)
        logger.info(f"   Initial page loaded: success={result.success}, html={len(result.html) if result.html else 0}")

        # Bước 2: Execute JS để trigger AJAX POST
        logger.info(f"📡 Executing AJAX POST via js_code...")
        ajax_config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            session_id=session_id,
            js_code=[js_ajax_post],
            js_only=True,
            wait_for=f"js:{wait_js}",
            page_timeout=30000,
        )
        result2 = await crawler.arun(url=url, config=ajax_config)

        if result2.success:
            logger.info(f"✅ AJAX result loaded, HTML length: {len(result2.html) if result2.html else 0}")

            # Parse HTML to find news items
            if result2.html:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(result2.html, 'html.parser')

                # Tìm container ajax-result
                ajax_container = soup.find('div', id='vsd-ajax-result')
                if ajax_container:
                    logger.info(f"   ✅ Found AJAX container, length: {len(str(ajax_container))}")
                    search_area = ajax_container
                else:
                    logger.info(f"   ⚠ No AJAX container found, searching entire page...")
                    search_area = soup

                # Extract news items
                for li in search_area.find_all('li'):
                    h3 = li.find('h3')
                    if not h3:
                        continue
                    a = h3.find('a')
                    if not a:
                        continue
                    title = a.get_text(strip=True)
                    href = a.get('href', '')
                    if not title or not href:
                        continue

                    # Check pattern CODE:
                    code_match = re.search(r'([A-Z0-9]{2,10}):', title)
                    if not code_match:
                        continue

                    code = code_match.group(1)
                    if not href.startswith('http'):
                        href = "https://www.vsd.vn" + href

                    # Extract date
                    time_div = li.find('div', class_='time-news')
                    date_text = None
                    if time_div:
                        dm = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', time_div.get_text())
                        date_text = dm.group(1) if dm else None

                    news_items.append({
                        'code': code,
                        'title': title,
                        'url': href,
                        'date': date_text
                    })

                logger.info(f"   ✅ Found {len(news_items)} items with stock codes")
                for item in news_items[:10]:
                    logger.info(f"      - {item['code']}: {item['title'][:60]}... ({item['date']})")

            # Cũng print markdown để xem
            if result2.markdown:
                md = result2.markdown.raw_markdown
                # Tìm phần markdown có chứa news items (sau AJAX)
                code_count = len(re.findall(r'[A-Z0-9]{2,10}:', md))
                logger.info(f"   📝 Markdown length: {len(md)}, stock code matches: {code_count}")
        else:
            logger.error(f"❌ AJAX request failed: {result2.error_message}")

        # Cleanup session
        try:
            await crawler.crawler_strategy.kill_session(session_id)
        except Exception:
            pass

    return news_items


# ============================================================================
# STEP 2: Crawl chi tiết bài viết VSD
#
# Cấu trúc HTML chi tiết VSD:
# <div class="col-md-4 item-info">Label:</div>
# <div class="col-md-8 item-info item-info-main">Value</div>
# ============================================================================
async def test_detail_page(article_url: str):
    """Crawl bài viết chi tiết VSD."""
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
    from crawl4ai import JsonCssExtractionStrategy

    logger.info("")
    logger.info("=" * 70)
    logger.info(f"STEP 2: Crawl chi tiết bài viết")
    logger.info(f"   URL: {article_url}")
    logger.info("=" * 70)

    # Schema cho detail page: extract label-value pairs
    # Trang VSD dùng: <div class="col-md-4 item-info">Label</div>
    #                 <div class="col-md-8 item-info item-info-main">Value</div>
    # Chúng nằm trong các div.row hoặc trực tiếp trong main content
    detail_schema = {
        "name": "VSD Detail Fields",
        "baseSelector": ".detail-info .row, .info-detail .row, main .row",
        "fields": [
            {
                "name": "label",
                "selector": ".col-md-4, .item-info:first-child",
                "type": "text"
            },
            {
                "name": "value",
                "selector": ".col-md-8, .item-info-main",
                "type": "text"
            }
        ]
    }

    browser_config = BrowserConfig(headless=True, java_script_enabled=True)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        extraction_strategy=JsonCssExtractionStrategy(detail_schema),
        page_timeout=30000,
    )

    info = {
        'tên_tổ_chức_đăng_ký': None,
        'tên_chứng_khoán': None,
        'mã_chứng_khoán': None,
        'mã_isin': None,
        'nơi_giao_dịch': None,
        'loại_chứng_khoán': None,
        'ngày_đăng_ký_cuối': None,
        'lý_do_mục_đích': None,
        'tỷ_lệ_thực_hiện': None,
        'thời_gian_thực_hiện': None,
        'địa_điểm_thực_hiện': None,
    }

    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(url=article_url, config=run_config)

        if not result.success:
            logger.error(f"❌ Failed to load: {result.error_message}")
            return info

        logger.info(f"✅ Detail page loaded successfully")
        logger.info(f"   HTML length: {len(result.html) if result.html else 0}")

        # === Approach A: CSS Extraction Strategy ===
        css_count = 0
        if result.extracted_content:
            rows = json.loads(result.extracted_content)
            logger.info(f"   📊 CSS Strategy extracted {len(rows)} rows")

            for row in rows:
                label = (row.get('label') or '').strip().lower()
                value = (row.get('value') or '').strip()
                if not label or not value:
                    continue

                # Log mỗi field tìm được
                logger.info(f"      [{label[:40]}] → [{value[:60]}]")

                if ('tên tổ chức đăng ký' in label or 'tên tcđkck' in label or 'tcđkck' in label) and not info['tên_tổ_chức_đăng_ký']:
                    info['tên_tổ_chức_đăng_ký'] = value
                    css_count += 1
                elif 'tên chứng khoán' in label and not info['tên_chứng_khoán']:
                    info['tên_chứng_khoán'] = value
                    css_count += 1
                elif ('mã chứng khoán' in label or 'mã ck' in label) and not info['mã_chứng_khoán']:
                    info['mã_chứng_khoán'] = value
                    css_count += 1
                elif 'mã isin' in label and not info['mã_isin']:
                    info['mã_isin'] = value
                    css_count += 1
                elif 'nơi giao dịch' in label and not info['nơi_giao_dịch']:
                    info['nơi_giao_dịch'] = value
                    css_count += 1
                elif 'loại chứng khoán' in label and not info['loại_chứng_khoán']:
                    info['loại_chứng_khoán'] = value
                    css_count += 1
                elif 'ngày đăng ký' in label and 'cuối' in label and not info['ngày_đăng_ký_cuối']:
                    info['ngày_đăng_ký_cuối'] = value
                    css_count += 1
                elif ('lý do' in label or 'mục đích' in label) and not info['lý_do_mục_đích']:
                    info['lý_do_mục_đích'] = value
                    css_count += 1
                elif 'tỷ lệ' in label and 'thực hiện' in label and not info['tỷ_lệ_thực_hiện']:
                    info['tỷ_lệ_thực_hiện'] = value
                    css_count += 1
                elif 'thời gian' in label and 'thực hiện' in label and not info['thời_gian_thực_hiện']:
                    info['thời_gian_thực_hiện'] = value
                    css_count += 1
                elif 'địa điểm' in label and 'thực hiện' in label and not info['địa_điểm_thực_hiện']:
                    info['địa_điểm_thực_hiện'] = value
                    css_count += 1

        logger.info(f"   {'✅' if css_count else '⚠'} CSS Strategy: {css_count} fields extracted")

        # === Approach B: Raw HTML with BeautifulSoup (tương tự fetch_vsd.py) ===
        html_count = 0
        if result.html:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(result.html, 'html.parser')

            # Trực tiếp dùng col-md-4 / col-md-8 (giống fetch_vsd.py)
            label_divs = soup.find_all('div', class_='col-md-4')
            logger.info(f"   🔍 Found {len(label_divs)} col-md-4 divs in HTML")

            for label_div in label_divs:
                label = label_div.get_text(strip=True).lower()
                value_div = label_div.find_next('div', class_='col-md-8')
                if not value_div:
                    continue
                value = value_div.get_text(strip=True)
                if not value:
                    continue

                if ('tên tổ chức đăng ký' in label or 'tên tcđkck' in label) and not info['tên_tổ_chức_đăng_ký']:
                    info['tên_tổ_chức_đăng_ký'] = value
                    html_count += 1
                elif 'tên chứng khoán' in label and not info['tên_chứng_khoán']:
                    info['tên_chứng_khoán'] = value
                    html_count += 1
                elif ('mã chứng khoán' in label or 'mã ck' in label) and not info['mã_chứng_khoán']:
                    info['mã_chứng_khoán'] = value
                    html_count += 1
                elif 'mã isin' in label and not info['mã_isin']:
                    info['mã_isin'] = value
                    html_count += 1
                elif 'nơi giao dịch' in label and not info['nơi_giao_dịch']:
                    info['nơi_giao_dịch'] = value
                    html_count += 1
                elif 'loại chứng khoán' in label and not info['loại_chứng_khoán']:
                    info['loại_chứng_khoán'] = value
                    html_count += 1
                elif 'ngày đăng ký' in label and 'cuối' in label and not info['ngày_đăng_ký_cuối']:
                    info['ngày_đăng_ký_cuối'] = value
                    html_count += 1
                elif ('lý do' in label or 'mục đích' in label) and not info['lý_do_mục_đích']:
                    info['lý_do_mục_đích'] = value
                    html_count += 1
                elif 'tỷ lệ' in label and 'thực hiện' in label and not info['tỷ_lệ_thực_hiện']:
                    info['tỷ_lệ_thực_hiện'] = value
                    html_count += 1
                elif 'thời gian' in label and 'thực hiện' in label and not info['thời_gian_thực_hiện']:
                    info['thời_gian_thực_hiện'] = value
                    html_count += 1
                elif 'địa điểm' in label and 'thực hiện' in label and not info['địa_điểm_thực_hiện']:
                    info['địa_điểm_thực_hiện'] = value
                    html_count += 1

        logger.info(f"   {'✅' if html_count else '⚠'} HTML (BS4) fallback: {html_count} additional fields extracted")

        # === Approach C: Markdown text parsing ===
        md_count = 0
        if result.markdown:
            md_text = result.markdown.raw_markdown
            logger.info(f"   📝 Markdown length: {len(md_text)}")

            # Print markdown preview để xem quality
            logger.info("   --- Markdown preview (first 1000 chars) ---")
            for line in md_text[:1000].split('\n'):
                logger.info(f"   | {line}")
            logger.info("   --- end preview ---")

            field_patterns = {
                'tên_tổ_chức_đăng_ký': r'(?:Tên tổ chức đăng ký|Tên TCĐKCK)[:\s|]+([^\n|]+)',
                'tên_chứng_khoán': r'Tên chứng khoán[:\s|]+([^\n|]+)',
                'mã_chứng_khoán': r'(?:Mã chứng khoán|Mã CK)[:\s|]+([A-Z0-9]+)',
                'mã_isin': r'Mã ISIN[:\s|]+([A-Z0-9]+)',
                'nơi_giao_dịch': r'Nơi giao dịch[:\s|]+([^\n|]+)',
                'loại_chứng_khoán': r'Loại chứng khoán[:\s|]+([^\n|]+)',
                'ngày_đăng_ký_cuối': r'Ngày đăng ký cuối cùng[:\s|]+([^\n|]+)',
                'lý_do_mục_đích': r'(?:Lý do|Mục đích)[:\s|]+([^\n|]+)',
                'tỷ_lệ_thực_hiện': r'Tỷ lệ thực hiện[:\s|]+([^\n|]+)',
                'thời_gian_thực_hiện': r'Thời gian thực hiện[:\s|]+([^\n|]+)',
                'địa_điểm_thực_hiện': r'Địa điểm thực hiện[:\s|]+([^\n|]+)',
            }

            for field, pattern in field_patterns.items():
                if info[field]:
                    continue
                match = re.search(pattern, md_text, re.IGNORECASE)
                if match:
                    val = match.group(1).strip()
                    if val and val != '--':
                        info[field] = val
                        md_count += 1

        logger.info(f"   {'✅' if md_count else '⚠'} Markdown parsing: {md_count} additional fields extracted")

    return info


# ============================================================================
# MAIN
# ============================================================================
async def main():
    logger.info("🚀 Test crawl4ai cho VSD - v2 (AJAX POST support)")
    logger.info(f"   Thời gian: {datetime.now().isoformat()}")
    logger.info("")

    # STEP 1: Lấy danh sách tin
    news_items = await test_list_page()

    # STEP 2: Extract chi tiết từ bài đầu tiên
    if news_items:
        first_article = news_items[0]
        logger.info(f"\n🔍 Testing detail extraction for: {first_article['code']}")
        detail = await test_detail_page(first_article['url'])
    else:
        logger.warning("⚠ Không có bài viết từ list. Thử trực tiếp URL mẫu...")
        # Dùng URL bài viết mẫu (thay bằng URL thực nếu cần)
        sample_urls = [
            "https://www.vsd.vn/vi/tin-thi-truong-co-so/chi-tiet/GEX-Thong-bao-ve-ngay-dang-ky-cuoi-cung-de-thuc-hien-quyen-tham-du-Dai-hoi-dong-co-dong-thuong-nien-nam-2026",
        ]
        # Tìm URL thực bằng cách lấy link từ markdown/HTML
        detail = await test_detail_page(sample_urls[0])

    # Summary
    logger.info("")
    logger.info("=" * 70)
    logger.info("📋 KẾT QUẢ EXTRACT CHI TIẾT:")
    logger.info("=" * 70)
    filled = 0
    total = len(detail)
    for key, value in detail.items():
        status = "✅" if value else "❌"
        display_val = (str(value)[:80] + "...") if value and len(str(value)) > 80 else value
        logger.info(f"   {status} {key}: {display_val}")
        if value:
            filled += 1

    logger.info(f"\n   📊 Tổng kết: {filled}/{total} fields extracted")
    logger.info(f"   📰 Tổng bài viết từ list page: {len(news_items)}")

    logger.info("")
    logger.info("=" * 70)
    logger.info("📊 ĐÁNH GIÁ CRAWL4AI CHO VSD")
    logger.info("=" * 70)
    logger.info("")

    if len(news_items) > 0:
        logger.info("   ✅ AJAX POST pagination: HOẠT ĐỘNG")
    else:
        logger.info("   ❌ AJAX POST pagination: CẦN DEBUG THÊM")

    if filled > 5:
        logger.info(f"   ✅ Detail extraction: TỐT ({filled}/{total} fields)")
    elif filled > 0:
        logger.info(f"   ⚠ Detail extraction: CẦN CẢI TIẾN ({filled}/{total} fields)")
    else:
        logger.info(f"   ❌ Detail extraction: KHÔNG HOẠT ĐỘNG ({filled}/{total} fields)")

    logger.info("")
    logger.info("   So sánh với fetch_vsd.py:")
    logger.info("   + crawl4ai: Tự động JS rendering, session management")
    logger.info("   + crawl4ai: CSS/LLM Extraction Strategy built-in")
    logger.info("   - crawl4ai: Chậm hơn (headless browser vs requests)")
    logger.info("   - crawl4ai: Cần Playwright setup (phức tạp cho Docker)")
    logger.info("   = Kết luận: Phù hợp cho trang cần JS rendering,")
    logger.info("     nhưng VSD có thể crawl hiệu quả hơn bằng requests+BS4")

    # Output JSON summary
    summary = {
        'test_time': datetime.now().isoformat(),
        'list_items_found': len(news_items),
        'detail_fields_extracted': filled,
        'detail_fields_total': total,
        'news_items_sample': news_items[:5] if news_items else [],
        'detail_data': detail,
    }
    print("\n--- JSON SUMMARY ---")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
