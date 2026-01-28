
import os
import sys
import logging

# Ensure app imports work
sys.path.append(os.getcwd())

from app.services.seeking_alpha_service import seeking_alpha_service

# Sample Mock HTML similar to what was seen in TRU report
SAMPLE_HTML_BLOB = """
<p data-eci="true"><figure class="getty-figure" data-type="getty-image"><picture> <img src="https://static.seekingalpha.com/cdn/s3/uploads/getty_images/2177802284/image_2177802284.jpg?io=getty-c-w630" alt="Businessman evaluate customer statistical data with credit score icon. Credit score concept. Online credit score ranking check. Loan, mortgage and payment cards." data-id="2177802284" data-type="getty-image" width="1536" height="864" srcset="https://static.seekingalpha.com/cdn/s3/uploads/getty_images/2177802284/image_2177802284.jpg?io=getty-c-w1536 1536w, https://static.seekingalpha.com/cdn/s3/uploads/getty_images/2177802284/image_2177802284.jpg?io=getty-c-w1280 1280w, https://static.seekingalpha.com/cdn/s3/uploads/getty_images/2177802284/image_2177802284.jpg?io=getty-c-w1080 1080w, https://static.seekingalpha.com/cdn/s3/uploads/getty_images/2177802284/image_2177802284.jpg?io=getty-c-w750 750w, https://static.seekingalpha.com/cdn/s3/uploads/getty_images/2177802284/image_2177802284.jpg?io=getty-c-w640 640w, https://static.seekingalpha.com/cdn/s3/uploads/getty_images/2177802284/image_2177802284.jpg?io=getty-c-w480 480w, https://static.seekingalpha.com/cdn/s3/uploads/getty_images/2177802284/image_2177802284.jpg?io=getty-c-w320 320w, https://static.seekingalpha.com/cdn/s3/uploads/getty_images/2177802284/image_2177802284.jpg?io=getty-c-w240 240w" sizes="(max-width: 768px) calc(100vw - 36px), (max-width: 1024px) calc(100vw - 132px), (max-width: 1200px) calc(100vw - 666px), (max-width: 1308px) calc(100vw - 708px), 600px" fetchpriority="high"> </picture><figcaption><p class="item-caption"> </p> <p class="item-credits">phakphum patjangkata/iStock via Getty Images</p></figcaption></figure></p> <div class="inline_ad_placeholder"></div> <h2><strong>Introduction </strong></h2> <p>TransUnion (<span class="ticker-hover-wrapper"><a href="https://seekingalpha.com/symbol/TRU" title="TransUnion">TRU</a></span>) has an underrated business model of providing credit reporting to both businesses and consumers. By providing a scoring system useful to lenders, TransUnion has amassed a database on nearly one seventh of<span class="paywall-full-content"> the global population and continues to grow in the double-digits each year.</span></p> <p class="paywall-full-content">Since I last covered the stock in December 2024, TransUnion has been a rather disappointing investment, having returned -7.5% over the period. That said, I think the valuation investors are paying today for the stock still makes sense and the latest earnings highlight that there is still growth to be had.</p>
"""

EXPECTED_TEXT_SUBSTRING = "TransUnion (TRU) has an underrated business model"
EXPECTED_NO_TAGS = "<p class="

def test_cleaner():
    print("Running Cleaner Test...")
    cleaned = seeking_alpha_service._clean_html(SAMPLE_HTML_BLOB)
    
    print("-" * 40)
    print("ORIGINAL LENGTH:", len(SAMPLE_HTML_BLOB))
    print("CLEANED LENGTH:", len(cleaned))
    print("-" * 40)
    print(cleaned)
    print("-" * 40)
    
    if EXPECTED_TEXT_SUBSTRING in cleaned:
        print("[PASS] Core text preserved.")
    else:
        print("[FAIL] Core text missing.")
        
    if EXPECTED_NO_TAGS not in cleaned and "<img" not in cleaned:
        print("[PASS] HTML tags stripped.")
    else:
        print("[FAIL] HTML tags still present.")

    if "paywall-full-content" not in cleaned:
        print("[PASS] Paywall class artifacts removed.")
    else:
        print("[FAIL] Paywall class artifacts present.")

if __name__ == "__main__":
    test_cleaner()
