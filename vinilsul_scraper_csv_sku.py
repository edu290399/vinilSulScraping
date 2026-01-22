import csv
import random
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional, Set, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import requests


BASE_URL = "https://www.vinilsul.com.br/categoria-produto/portfolio-estamparia-digital/"


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
]


@dataclass
class Produto:
    nome: str
    sku: str
    categoria: str
    marca: str
    descricao: str
    imagens: str
    url: str


class VinilSulScraper:
    def __init__(
        self,
        base_url: str = BASE_URL,
        output_csv: str = "produtos_vinilsul.csv",
        images_dir: str = "imagens_vinilsul",
        min_delay: float = 1.5,
        max_delay: float = 4.0,
    ) -> None:
        self.base_url = base_url
        self.output_csv = output_csv
        self.images_dir = Path(images_dir)
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.user_agent = random.choice(USER_AGENTS)

    def _sleep(self) -> None:
        time.sleep(random.uniform(self.min_delay, self.max_delay))

    def _parse_product_links(self, html: str) -> List[str]:
        soup = BeautifulSoup(html, "html.parser")
        links: List[str] = []
        for anchor in soup.select("li.product a.woocommerce-LoopProduct-link, li.product a.woocommerce-loop-product__link"):
            href = anchor.get("href")
            if href:
                links.append(href)
        # Fallback: any product card link
        if not links:
            for anchor in soup.select("li.product a"):
                href = anchor.get("href")
                if href and "/produto/" in href:
                    links.append(href)
        return list(dict.fromkeys(links))

    def _get_next_page(self, html: str) -> Optional[str]:
        soup = BeautifulSoup(html, "html.parser")
        next_link = soup.select_one("a.next, li.next a, a.page-numbers.next")
        if next_link and next_link.get("href"):
            return next_link["href"]
        return None

    def _slugify(self, value: str) -> str:
        value = value.strip().lower()
        value = re.sub(r"[^a-z0-9]+", "-", value)
        return value.strip("-")

    def _filename_from_url(self, url: str, index: int) -> str:
        parsed = urlparse(url)
        name = Path(parsed.path).name
        if not name:
            return f"imagem_{index}.jpg"
        if "." not in name:
            name = f"{name}.jpg"
        return re.sub(r"[^\w.\-]", "_", name)

    def _download_images(self, image_urls: List[str], product_name: str, product_url: str) -> List[str]:
        if not image_urls:
            return []
        slug_source = product_name or Path(urlparse(product_url).path).name
        folder_name = self._slugify(slug_source) or "produto"
        product_dir = self.images_dir / folder_name
        product_dir.mkdir(parents=True, exist_ok=True)

        relative_paths: List[str] = []
        for idx, img_url in enumerate(image_urls, start=1):
            try:
                filename = self._filename_from_url(img_url, idx)
                file_path = product_dir / filename
                if not file_path.exists():
                    response = requests.get(
                        img_url,
                        headers={"User-Agent": self.user_agent},
                        timeout=30,
                        stream=True,
                    )
                    response.raise_for_status()
                    with open(file_path, "wb") as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                relative_paths.append(str(Path(self.images_dir.name) / folder_name / filename))
            except Exception as exc:
                print(f"Erro ao baixar imagem {img_url}: {exc}")
        return relative_paths

    def _extract_product_details(self, html: str, url: str) -> Tuple[Produto, List[str]]:
        soup = BeautifulSoup(html, "html.parser")
        title_el = soup.select_one("h2.product_title.entry-title") or soup.select_one("h1.product_title") or soup.select_one("h1")
        name = title_el.get_text(strip=True) if title_el else ""

        sku = ""
        sku_el = soup.select_one(".sku")
        if sku_el:
            sku = sku_el.get_text(strip=True)

        categoria = ""
        marca = ""
        meta = soup.select_one("div.product_meta")
        if meta:
            categorias = [a.get_text(strip=True) for a in meta.select("span.posted_in a") if a.get_text(strip=True)]
            if categorias:
                categoria = " > ".join(categorias)
            for span in meta.select("span"):
                span_text = span.get_text(" ", strip=True)
                if "Marca" in span_text:
                    marcas = [a.get_text(strip=True) for a in span.select("a") if a.get_text(strip=True)]
                    if marcas:
                        marca = ", ".join(dict.fromkeys(marcas))
                    break
        if not categoria:
            breadcrumbs = [crumb.get_text(strip=True) for crumb in soup.select("nav.woocommerce-breadcrumb a")]
            categoria = " > ".join([b for b in breadcrumbs if b])

        descricao_parts = []
        short_desc = soup.select_one("div.woocommerce-product-details__short-description")
        if short_desc:
            descricao_parts.append(short_desc.get_text(" ", strip=True))
        long_desc = soup.select_one("#tab-description") or soup.select_one("div.woocommerce-Tabs-panel--description")
        if long_desc:
            descricao_parts.append(long_desc.get_text(" ", strip=True))
        descricao = " | ".join([p for p in descricao_parts if p])

        images: List[str] = []
        for img in soup.select("figure.woocommerce-product-gallery img, div.woocommerce-product-gallery img"):
            src = img.get("data-src") or img.get("src")
            if src:
                images.append(src)
        image_urls = list(dict.fromkeys(images))

        return Produto(
            nome=name,
            sku=sku,
            categoria=categoria,
            marca=marca,
            descricao=descricao,
            imagens="",
            url=url,
        ), image_urls

    def _write_csv(self, produtos: List[Produto]) -> None:
        with open(self.output_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["nome", "sku", "categoria", "marca", "descricao", "imagens", "url"],
            )
            writer.writeheader()
            for produto in produtos:
                writer.writerow(asdict(produto))

    def run(self) -> None:
        produtos: List[Produto] = []
        seen_links: Set[str] = set()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=self.user_agent,
                locale="pt-BR",
            )
            page = context.new_page()

            current_url = self.base_url
            page_index = 1
            while current_url:
                print(f"Processando página {page_index}... {current_url}")
                try:
                    page.goto(current_url, wait_until="domcontentloaded", timeout=60000)
                except PlaywrightTimeoutError:
                    print(f"Timeout ao carregar página: {current_url}")
                    break

                self._sleep()
                html = page.content()
                product_links = self._parse_product_links(html)
                if not product_links:
                    print("Nenhum produto encontrado. Encerrando.")
                    break

                for link in product_links:
                    if link in seen_links:
                        continue
                    seen_links.add(link)
                    print(f"Extraindo produto: {link}")
                    try:
                        page.goto(link, wait_until="domcontentloaded", timeout=60000)
                        self._sleep()
                        product_html = page.content()
                        produto, image_urls = self._extract_product_details(product_html, link)
                        imagens_rel = self._download_images(image_urls, produto.nome, link)
                        produto.imagens = ",".join(imagens_rel)
                        produtos.append(produto)
                    except Exception as exc:
                        print(f"Erro ao extrair produto {link}: {exc}")
                        continue

                next_url = self._get_next_page(html)
                if not next_url or next_url == current_url:
                    break
                current_url = next_url
                page_index += 1

            browser.close()

        self._write_csv(produtos)
        print(f"Concluído! {len(produtos)} produtos salvos em {self.output_csv}")


if __name__ == "__main__":
    VinilSulScraper().run()

