import os

import requests

from wbmbot.utils import setup_loggers

setup_loggers()

import logging
logger = logging.getLogger("app")

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from wbmbot import User, ConfigLoader, FlatScraper, ApplicationManager


def main():
    # Load or interactively collect user data/configuration
    user_input = ConfigLoader(config_var="USER_CONFIG")
    user_data = user_input.load_user_data()
    user = User(user_data)

    # Configure Chrome WebDriver options
    options = Options()
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-gpu")
    options.add_argument("--headless=new")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument('--log-level=3')

    with webdriver.Chrome(options=options) as driver:

        start_url = "https://www.wbm.de/wohnungen-berlin/angebote/"

        # Initialize application handler
        app_manager = ApplicationManager(driver, user)

        # Initialize scraper and load starting page
        scraper = FlatScraper(driver, start_url)
        scraper.load_start_page()

        # Scrape flats from the website
        flats = scraper.get_flats()

        # Iterate over flats and apply if they match user criteria
        for flat in flats:
            if flat.matches_criteria(user):
                flat_details = scraper.get_details(flat.detail_link)
                flat.update_details(flat_details)
                if flat.within_range(user):
                    logger.info(f"Flat {flat.title} matches criteria... applying...")
                    if app_manager.apply(flat):
                        token = os.getenv("TELEGRAM_BOT_TOKEN")
                        chat_id = os.getenv("TELEGRAM_CHAT_ID")
                        if token and chat_id:
                            try:
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
                                
                                requests.post(
                                    f"https://api.telegram.org/bot{token}/sendMessage",
                                    data={"chat_id": chat_id, "text": "\n".join(msg_parts)},
                                    timeout=10
                                )
                                logger.info(f"Telegram notification sent for: {flat.title}")
                            except Exception as e:
                                logger.warning(f"Failed to send Telegram notification: {e}")
            else:
                logger.info(f"Flat '{flat.title}' does not meet search criteria... skipping...")


if __name__ == "__main__":
    main()