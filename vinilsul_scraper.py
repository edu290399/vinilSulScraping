import json
import time
from collections import deque
from typing import Dict, List, Optional, Set

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin


# =============================
# Configuração e constantes
# =============================

# URLs iniciais (páginas de categorias/listagens) para começar o scraping
start_urls: List[str] = [
    "https://www.vinilsul.com.br/categoria-produto/suprimentos/",
]

# Cabeçalhos para simular um navegador real
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
}

# Atraso mínimo entre requisições, em segundos (boas práticas)
REQUEST_DELAY_SECONDS = 1.0


def create_session() -> requests.Session:
    """
    Cria uma sessão HTTP com cabeçalhos padrão.
    """
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    return session


def fetch_html(session: requests.Session, url: str, timeout: int = 30) -> Optional[str]:
    """
    Faz uma requisição GET para a URL e retorna o conteúdo HTML como string.
    Aplica atraso mínimo entre requisições e trata erros de rede.
    """
    try:
        response = session.get(url, timeout=timeout)
        time.sleep(REQUEST_DELAY_SECONDS)
        response.raise_for_status()
        return response.text
    except requests.RequestException:
        return None


def absolute_url(base_url: str, maybe_relative_url: str) -> str:
    """Gera URL absoluta com base na URL base e um caminho possivelmente relativo."""
    return urljoin(base_url, maybe_relative_url)


# =============================
# Módulo 1: Descoberta de Links de Produtos com Paginação
# =============================

def discover_product_urls(start_urls: List[str]) -> List[str]:
    """
    A partir de uma lista de URLs de listagem (categorias), navega com paginação
    e coleta todos os links de produtos.

    - Encontra links de produtos usando o seletor: a.button.product_type_simple (atributo href)
    - Encontra o link de próxima página usando: a.next.page-numbers
    - Evita duplicidades e loops de paginação
    """
    session = create_session()

    product_urls: Set[str] = set()
    visited_listing_pages: Set[str] = set()
    queue: deque[str] = deque(start_urls)

    while queue:
        listing_url = queue.popleft()
        if listing_url in visited_listing_pages:
            continue
        visited_listing_pages.add(listing_url)

        html = fetch_html(session, listing_url)
        if html is None:
            # Falha ao obter a página de listagem; segue para a próxima
            continue

        soup = BeautifulSoup(html, "lxml")

        # a) Coletar links de produtos
        for a in soup.select("a.button.product_type_simple"):
            href = a.get("href")
            if not href:
                continue
            full_url = absolute_url(listing_url, href)
            product_urls.add(full_url)

        # b) Descobrir próxima página de paginação
        next_link = soup.select_one("a.next.page-numbers")
        if next_link is not None:
            next_href = next_link.get("href")
            if next_href:
                next_url = absolute_url(listing_url, next_href)
                if next_url not in visited_listing_pages:
                    queue.append(next_url)

    return sorted(product_urls)


# =============================
# Módulo 2: Extração Detalhada de Dados do Produto
# =============================

def scrape_product_details(product_url: str) -> Dict:
    """
    Recebe a URL de um produto, baixa o HTML e extrai os dados conforme seletores.
    Campos extraídos:
      - title: h2.product_title.entry-title
      - short_description: div.woocommerce-product-details__short-description
      - categories: span.posted_in a (lista de textos)
      - tags: span.tagged_as a (lista de textos)
      - advantages: em div#tab-description, após h2 com texto "Vantagens", extrai li subsequentes
      - technical_info: em div#tab-description, p contendo strong "Informações Técnicas:"; parseia linhas chave: valor
      - url: a própria URL do produto
    """
    session = create_session()
    html = fetch_html(session, product_url)
    if html is None:
        return {
            "url": product_url,
            "title": None,
            "short_description": None,
            "categories": [],
            "tags": [],
            "advantages": [],
            "technical_info": {},
        }

    soup = BeautifulSoup(html, "lxml")

    # title
    try:
        title_el = soup.select_one("h2.product_title.entry-title")
        title = title_el.get_text(strip=True) if title_el else None
    except Exception:
        title = None

    # short_description
    try:
        sd_el = soup.select_one("div.woocommerce-product-details__short-description")
        short_description = sd_el.get_text(" ", strip=True) if sd_el else None
    except Exception:
        short_description = None

    # categories
    categories: List[str] = []
    try:
        cat_span = soup.select_one("span.posted_in")
        if cat_span is not None:
            for a in cat_span.select("a"):
                text = (a.get_text(strip=True) or "").strip()
                if text:
                    categories.append(text)
    except Exception:
        categories = []

    # tags
    tags: List[str] = []
    try:
        tag_span = soup.select_one("span.tagged_as")
        if tag_span is not None:
            for a in tag_span.select("a"):
                text = (a.get_text(strip=True) or "").strip()
                if text:
                    tags.append(text)
    except Exception:
        tags = []

    # advantages
    advantages: List[str] = []
    try:
        desc_tab = soup.select_one("div#tab-description")
        if desc_tab is not None:
            # localiza h2 que contenha o texto "Vantagens"
            h2_list = desc_tab.select("h2")
            target_h2 = None
            for h2 in h2_list:
                heading_text = h2.get_text(" ", strip=True).lower()
                if "vantagens" in heading_text:
                    target_h2 = h2
                    break

            if target_h2 is not None:
                # procura a primeira lista após o h2
                ul = target_h2.find_next("ul")
                if ul is not None:
                    for li in ul.select("li"):
                        txt = li.get_text(" ", strip=True)
                        if txt:
                            advantages.append(txt)
    except Exception:
        advantages = []

    # technical_info
    technical_info: Dict[str, str] = {}
    try:
        desc_tab = desc_tab or soup.select_one("div#tab-description")
        if desc_tab is not None:
            p_list = desc_tab.select("p")
            target_p = None
            for p in p_list:
                strong = p.find("strong")
                if strong is not None:
                    strong_text = strong.get_text(" ", strip=True).lower()
                    if "informações técnicas" in strong_text:
                        target_p = p
                        break

            if target_p is not None:
                # Converter conteúdo HTML do parágrafo e quebrar por <br>
                raw_html = target_p.decode_contents()
                # Normalizar tags <br>
                normalized = raw_html.replace("<br/>", "<br>").replace("<br />", "<br>")
                # Quebrar por <br>
                parts = [s.strip() for s in normalized.split("<br>")]
                for part in parts:
                    if not part:
                        continue
                    # Remover HTML restante e obter texto limpo
                    part_text = BeautifulSoup(part, "lxml").get_text(" ", strip=True)
                    if not part_text:
                        continue
                    # Ignora a linha de título "Informações Técnicas:"
                    if part_text.lower().startswith("informações técnicas"):
                        continue
                    if ":" in part_text:
                        key, value = part_text.split(":", 1)
                        key = key.strip()
                        value = value.strip()
                        if key:
                            technical_info[key] = value
    except Exception:
        technical_info = {}

    return {
        "url": product_url,
        "title": title,
        "short_description": short_description,
        "categories": categories,
        "tags": tags,
        "advantages": advantages,
        "technical_info": technical_info,
    }


# =============================
# Módulo 3: Orquestração Principal e Geração do Arquivo
# =============================

def main() -> None:
    # 1) Descobrir todas as URLs de produtos
    print("Descobrindo URLs de produtos a partir das páginas iniciais...")
    product_urls = discover_product_urls(start_urls)
    print(f"Total de produtos encontrados: {len(product_urls)}")

    # 2) Percorrer os produtos e extrair detalhes
    results: List[Dict] = []
    total = len(product_urls)
    for idx, url in enumerate(product_urls, start=1):
        try:
            print(f"[{idx}/{total}] Extraindo: {url}")
            details = scrape_product_details(url)
            results.append(details)
        except Exception:
            # Em caso de erro inesperado, registramos e continuamos
            results.append({
                "url": url,
                "title": None,
                "short_description": None,
                "categories": [],
                "tags": [],
                "advantages": [],
                "technical_info": {},
            })

    # 3) Salvar em JSON
    output_file = "vinilsul_produtos.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=4)

    print(f"Arquivo gerado: {output_file}")


if __name__ == "__main__":
    main()


