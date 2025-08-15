#!/usr/bin/env python3

import json
import logging
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("scraper.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


@dataclass
class RecipeData:
    recipe_id: int
    name: str
    profession: str
    skill_level: int
    patch_version: str
    icon_name: str
    materials: List[Dict[str, int]]
    result_item_id: int
    result_quantity: int
    url: str
    scraped_at: str
    difficulties: List[int]


class WowheadScraper:
    def __init__(self, headless: bool = True, timeout: int = 15):
        self.headless = headless
        self.timeout = timeout
        self.driver = None
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
        )

    def __enter__(self):
        self._setup_driver()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._cleanup()

    def _setup_driver(self):
        options = Options()
        if self.headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-plugins")
        options.add_argument("--disable-images")
        options.add_argument("--disable-javascript")
        options.add_argument("--disable-css")
        options.add_argument("--disable-animations")
        options.add_argument("--disable-web-security")
        options.add_argument("--disable-features=VizDisplayCompositor")
        options.add_argument("--memory-pressure-off")
        options.add_argument("--max_old_space_size=4096")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        try:
            self.driver = webdriver.Chrome(options=options)
            self.driver.set_page_load_timeout(self.timeout)
            self.driver.set_script_timeout(self.timeout)
            logger.info("WebDriver initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize WebDriver: {e}")
            raise

    def _cleanup(self):
        if self.driver:
            try:
                self.driver.quit()
                logger.info("WebDriver closed successfully")
            except Exception as e:
                logger.warning(f"Error closing WebDriver: {e}")

    def _extract_recipe_id(self, url: str) -> int:
        match = re.search(r"spell=(\d+)", url)
        if not match:
            raise ValueError(f"Could not extract recipe ID from URL: {url}")
        return int(match.group(1))

    def _extract_recipe_name(self, soup: BeautifulSoup) -> str:
        name_tag = soup.find("h1", class_="heading-size-1")
        if not name_tag:
            raise ValueError("Could not find recipe name")
        return name_tag.text.strip()

    def _extract_icon_name(self, soup: BeautifulSoup) -> str:
        icon_li = soup.select_one("li.icon-db-link ins[style]")
        if not icon_li or "background-image" not in icon_li.get("style", ""):
            return ""
        match = re.search(r'url\(["\']?(.*?)["\']?\)', icon_li["style"])
        if match:
            icon_url = match.group(1)
            return icon_url.split("/")[-1].split(".")[0]
        return ""

    def _extract_profession(self, soup: BeautifulSoup) -> str:
        breadcrumb = soup.select_one("div.page-controls")
        if not breadcrumb:
            return "Unknown"
        links = breadcrumb.find_all("a")
        if links:
            return links[-1].text.strip()
        return "Unknown"

    def _extract_skill_level(self, soup: BeautifulSoup) -> int:
        skill_divs = soup.find_all("div", attrs={"data-markup-content-target": "1"})
        for div in skill_divs:
            text = div.get_text(strip=True)
            match = re.search(r"Requires .*?\((\d+)\)", text)
            if match:
                return int(match.group(1))
        return 0

    def _extract_patch_version(self, soup: BeautifulSoup) -> str:
        patch_divs = soup.find_all("div", attrs={"data-markup-content-target": "1"})
        for div in patch_divs:
            span = div.find("span", class_="tip")
            if span:
                version = span.get_text(strip=True)
                if re.match(r"\d+\.\d+\.\d+", version):
                    return version
        return "0.0.0"

    def _extract_difficulties(self, soup: BeautifulSoup) -> List[int]:
        difficulty_divs = soup.find_all(
            "div", attrs={"data-markup-content-target": "1"}
        )
        for div in difficulty_divs:
            text = div.get_text(strip=True)
            if text.startswith("Difficulty:"):
                difficulties = []
                for class_name in ["r1", "r2", "r3", "r4"]:
                    span = div.find("span", class_=class_name)
                    if span:
                        try:
                            difficulty = int(span.get_text(strip=True))
                            difficulties.append(difficulty)
                        except ValueError:
                            logger.warning(
                                f"Invalid difficulty value in span {class_name}: {span.get_text(strip=True)}"
                            )
                return difficulties
        return []

    def _extract_materials(
        self, soup: BeautifulSoup, recipe_id: int
    ) -> List[Dict[str, int]]:
        materials = []
        tooltip_div = soup.select_one(f"div#tt{recipe_id}")
        if not tooltip_div:
            logger.warning(f"No tooltip found for recipe {recipe_id}")
            return materials
        reagents_label = tooltip_div.find(string=re.compile(r"Reagents:"))
        if not reagents_label:
            return materials
        reagents_div = reagents_label.find_next("div", class_="indent q1")
        if not reagents_div:
            return materials
        reagent_text = reagents_div.get_text(separator=" ", strip=True)
        links = reagents_div.find_all("a")
        for link in links:
            href = link.get("href", "")
            item_id_match = re.search(r"item=(\d+)", href)
            if item_id_match:
                item_id = int(item_id_match.group(1))
                material_name = link.text.strip()
                quantity_match = re.search(
                    re.escape(material_name) + r"\s*\((\d+)\)", reagent_text
                )
                quantity = int(quantity_match.group(1)) if quantity_match else 1
                materials.append({"itemId": item_id, "quantity": quantity})
        return materials

    def _extract_result_item(
        self, soup: BeautifulSoup, recipe_id: int
    ) -> Tuple[int, int]:
        tooltip_div = soup.select_one(f"div#tt{recipe_id}")
        if not tooltip_div:
            logger.warning(f"No tooltip found for recipe {recipe_id}")
            return 0, 1
        item_links = tooltip_div.select("a[href*='/item=']")
        if not item_links:
            logger.warning(f"No item links found in tooltip for recipe {recipe_id}")
            return 0, 1
        item_link = item_links[-1]
        item_id_match = re.search(r"item=(\d+)", item_link["href"])
        if not item_id_match:
            logger.warning(f"Could not extract item ID from link: {item_link['href']}")
            return 0, 1
        result_item_id = int(item_id_match.group(1))
        parent_span = item_link.find_parent("span")
        if parent_span:
            next_text = parent_span.next_sibling
            if next_text:
                text = str(next_text).strip()
                quantity_match = re.search(r"\((\d+)\)", text)
                if quantity_match:
                    result_quantity = int(quantity_match.group(1))
                    return result_item_id, result_quantity
        result_quantity = int(quantity_match.group(1)) if quantity_match else 1
        return result_item_id, result_quantity

    def scrape_recipe(self, url: str) -> Optional[RecipeData]:
        try:
            logger.info(f"Scraping recipe: {url}")
            self.driver.get(url)
            time.sleep(2)
            soup = BeautifulSoup(self.driver.page_source, "html.parser")
            recipe_id = self._extract_recipe_id(url)
            name = self._extract_recipe_name(soup)
            profession = self._extract_profession(soup)
            skill_level = self._extract_skill_level(soup)
            patch_version = self._extract_patch_version(soup)
            difficulties = self._extract_difficulties(soup)
            icon_name = self._extract_icon_name(soup)
            materials = self._extract_materials(soup, recipe_id)
            result_item_id, result_quantity = self._extract_result_item(soup, recipe_id)
            if skill_level == 0 and difficulties:
                skill_level = difficulties[0]
                logger.info(
                    f"Using first difficulty {skill_level} as skill_level for recipe {recipe_id}"
                )
            recipe_data = RecipeData(
                recipe_id=recipe_id,
                name=name,
                profession=profession,
                skill_level=skill_level,
                patch_version=patch_version,
                difficulties=difficulties,
                icon_name=icon_name,
                materials=materials,
                result_item_id=result_item_id,
                result_quantity=result_quantity,
                url=url,
                scraped_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
            logger.info(f"Successfully scraped recipe: {name}")
            return recipe_data
        except Exception as e:
            logger.error(f"Error scraping recipe {url}: {e}")
            return None

    def scrape_from_file(
        self,
        input_file: str,
        output_file: str,
        max_retries: int = 3,
        delay: float = 2.0,
    ) -> Dict[str, any]:
        input_path = Path(input_file)
        output_path = Path(output_file)
        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_file}")
        with open(input_path, "r") as f:
            urls = [line.strip() for line in f if line.strip()]
        logger.info(f"Found {len(urls)} URLs to scrape")
        stats = {"total_urls": len(urls), "successful": 0, "failed": 0, "recipes": []}
        for i, url in enumerate(urls, 1):
            logger.info(f"Processing {i}/{len(urls)}: {url}")
            recipe_data = None
            for attempt in range(max_retries):
                try:
                    recipe_data = self.scrape_recipe(url)
                    if recipe_data:
                        break
                    time.sleep(delay)
                except Exception as e:
                    logger.warning(f"Attempt {attempt + 1} failed for {url}: {e}")
                    if attempt < max_retries - 1:
                        time.sleep(delay * (attempt + 1))
            if recipe_data:
                stats["recipes"].append(asdict(recipe_data))
                stats["successful"] += 1
            else:
                stats["failed"] += 1
                logger.error(f"Failed to scrape after {max_retries} attempts: {url}")
        with open(output_path, "w") as f:
            json.dump(stats, f, indent=2)
        logger.info(
            f"Scraping completed. Success: {stats['successful']}, Failed: {stats['failed']}"
        )
        return stats


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Scrape WoW Classic SoD recipes from Wowhead"
    )
    parser.add_argument("input_file", help="File containing URLs to scrape")
    parser.add_argument("output_file", help="Output JSON file")
    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="Run browser in headless mode",
    )
    parser.add_argument(
        "--timeout", type=int, default=15, help="Page load timeout in seconds"
    )
    parser.add_argument(
        "--max-retries", type=int, default=3, help="Maximum retry attempts per URL"
    )
    parser.add_argument(
        "--delay", type=float, default=2.0, help="Delay between requests in seconds"
    )
    args = parser.parse_args()
    try:
        with WowheadScraper(headless=args.headless, timeout=args.timeout) as scraper:
            stats = scraper.scrape_from_file(
                args.input_file,
                args.output_file,
                max_retries=args.max_retries,
                delay=args.delay,
            )
            print(f"Scraping completed successfully!")
            print(f"Total URLs: {stats['total_urls']}")
            print(f"Successful: {stats['successful']}")
            print(f"Failed: {stats['failed']}")
    except KeyboardInterrupt:
        logger.info("Scraping interrupted by user")
    except Exception as e:
        logger.error(f"Scraping failed: {e}")
        return 1
    return 0


if __name__ == "__main__":
    exit(main())
