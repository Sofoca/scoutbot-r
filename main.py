import os

import requests

from wbmbot.utils import setup_loggers

setup_loggers()

import logging
logger = logging.getLogger("app")

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException
from urllib3.exceptions import ReadTimeoutError as Urllib3ReadTimeout
from wbmbot import User, ConfigLoader, FlatScraper, ApplicationManager


def send_telegram(text):
    """Send a Telegram notification. Returns True if delivered."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        return resp.ok
    except Exception as e:
        logger.warning(f"Failed to send Telegram notification: {e}")
        return False


def main():
    # Load or interactively collect user data/configuration
    user_input = ConfigLoader(config_var="USER_CONFIG")
    user_data = user_input.load_user_data()
    user = User(user_data)

    # Configure Chrome WebDriver options
    options = Options()
    options.page_load_strategy = "eager"  # don't wait for ads/trackers; DOM enough
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-gpu")
    options.add_argument("--headless=new")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-background-networking")
    options.add_argument('--log-level=3')

    with webdriver.Chrome(options=options) as driver:
        # Fail before Selenium's 120s HTTP client timeout so we can retry
        driver.set_page_load_timeout(45)

        start_url = "https://www.wbm.de/wohnungen-berlin/angebote/"

        # Initialize application handler
        app_manager = ApplicationManager(driver, user)

        # Initialize scraper and load starting page
        scraper = FlatScraper(driver, start_url)
        scraper.load_start_page()

        # Scrape flats from the website
        flats = scraper.get_flats()

        # Iterate over flats and apply if they match user criteria.
        # Transient Selenium/network errors on a single flat must not fail the
        # whole run (avoids CI failure spam); only abort on sustained failure.
        consecutive_failures = 0
        for flat in flats:
            try:
                if flat.matches_criteria(user):
                    flat_details = scraper.get_details(flat.detail_link)
                    flat.update_details(flat_details)
                    if flat.within_range(user):
                        logger.info(f"Flat {flat.title} matches criteria... applying...")
                        if app_manager.apply(flat):
                            # Build notification message with fallbacks for missing data
                            msg_parts = [
                                f"{user.first_name} applied to a flat! 🎉",
                                flat.title or "Unknown title",
                                f"📍 {flat.zip_code or 'N/A'} | {flat.size or 'N/A'}m² | {flat.rooms or 'N/A'} rooms",
                                f"💰 Total rent: {flat.total_rent or 'N/A'}€ | Base rent: {flat.base_rent or 'N/A'}€",
                                "🔴 WBS required" if flat.wbs else "✅ No WBS required",
                                f"🔗 {flat.detail_link or 'N/A'}",
                            ]
                            if flat.property_attrs:
                                msg_parts.append(f"🏠 {', '.join(flat.property_attrs)}")
                            if send_telegram("\n".join(msg_parts)):
                                logger.info(f"Telegram notification sent for: {flat.title}")
                    else:
                        logger.info(f"Flat {flat.title} does not meet criteria after details... skipping...")
                else:
                    logger.info(f"Flat '{flat.title}' does not meet search criteria... skipping...")
                consecutive_failures = 0
            except (WebDriverException, TimeoutError, Urllib3ReadTimeout) as e:
                consecutive_failures += 1
                logger.warning(f"Transient error on flat '{flat.title}'; skipping: {e}")
                if consecutive_failures >= 5:
                    logger.error("5 consecutive transient failures; aborting run")
                    raise


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Run failed")
        # Notify via Telegram and exit cleanly; only propagate (failing CI and
        # triggering the GitHub email) when the notification can't be delivered.
        detail = str(e).split("Stacktrace")[0].strip()
        if not send_telegram(f"⚠️ Flat scout run failed: {type(e).__name__}: {detail[:300]}"):
            raise