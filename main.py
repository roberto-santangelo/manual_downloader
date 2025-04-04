import os
import re
import shutil
from functools import partial
from multiprocessing import Pool
from urllib.parse import urlparse

import jinja2
import requests
from InquirerPy import prompt
from playwright.sync_api import sync_playwright
from pypdf import PdfWriter

print("https://www.manualeduso.it/sennheiser/xs-wireless-1/manuale")

TEMP_FOLDER = 'temp'
# PRINT_TEMPLATE = """<html><head><meta charset="UTF-8"><style>{{custom_css}}</style><style>{{base_css}}</style><style>{{page_css}}</style></head><body><a name="{{page}}"></a><div class="viewer-page"><div class="page-{{page}} pf w0 h0">{{content}}</div></div></body></html>"""

PRINT_TEMPLATE = """<html><head><meta charset="UTF-8"><style>{{custom_css}}</style><style>{{base_css}}</style><style>{{page_css}}</style></head><body><a name="{{page}}"></a><div class="viewer-page"><div class="pc pc1 w0 h0">{{content}}</div></div></body></html>"""

# bi x0 y0 w1 h1
# pf w0 h0 ORIGINALE

def create_folder_if_not_exists(folder: str) -> None:
    """Create folder if not exists"""
    if not os.path.exists(folder):
        os.makedirs(folder)


def sanitize(input_string: str) -> str:
    """
    Sanitize the input string by replacing '/' with '_'
    and removing any unwanted characters.

    Args:
        input_string (str): The string to be sanitized.

    Returns:
        str: The sanitized string.
    """
    # Replace '/' with '_'
    sanitized_string = input_string.replace('/', '_')

    # Remove any characters that are not alphanumeric or underscores
    sanitized_string = re.sub(r'[^\w_]', '', sanitized_string)

    return sanitized_string


def get_domain(url: str) -> str:
    """Extracts the base domain from a URL."""
    parsed_url = urlparse(url)
    base_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
    return base_domain


def get_manual_url() -> str:
    """Prompt input for Manual PDF url"""
    url_question = [{
        'type': 'input',
        'name': 'url',
        'message': 'Inserisci l\'url del manuale di cui vuoi il PDF:',
    }]
    url_answer = prompt(url_question)
    return url_answer.get('url').split('#')[0].split('?')[0]


def get_data(url: str) -> dict:
    """Process url and return a dictionary with the data"""
    html = requests.get(url).text
    file_id = re.search(r'viewer/([\d/]+)/1/bg1', html).group(1)
    pages = re.search(r'<title>(.*)\(.*?(\d+).*?\)</title>', html)
    title = pages.group(1).strip()
    total_pages = int(pages.group(2))

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url)

        page.wait_for_load_state("networkidle")
        css_url = page.locator("link[rel='stylesheet'][href*='_nuxt/manual']"
                               ).get_attribute("href")

        custom_css = requests.get(css_url).text

    return dict(file_id=file_id,
                title=title,
                total_pages=total_pages,
                custom_css=custom_css)


def replace_urls_to_absolute(url_viewer: str, content: str) -> str:
    """Get html content and replace url relatives for absolutes"""

    content = content.replace('src="', f'src="{url_viewer}')
    content = content.replace('src:url(', f'src:url({url_viewer}')
    return content


def get_html_page(domain: str, file_id: str, page: int) -> str:
    """Get html page from manualpdf.es"""
    url_viewer = f"{domain}/viewer/{file_id}/{page}/"

    # url return file, download it and read it
    content = requests.get(f"{url_viewer}page-{page}.page").text

    # replace relative links to absolute links
    content = replace_urls_to_absolute(url_viewer=url_viewer, content=content)

    return content


def generate_page(domain: str, file_id: str, page: int, content: str,
                  path: str, landscape: bool, custom_css: str):
    """Generate html page with jinja2 template"""
    url_viewer = f"{domain}/viewer/{file_id}/{page}/"
    template = jinja2.Template(PRINT_TEMPLATE)

    base_url = "https://www.manualeduso.it/css/base.css"
    base_css = requests.get(base_url).text
    base_css = replace_urls_to_absolute(url_viewer=url_viewer,
                                        content=base_css)

    page_url = f"https://www.manualeduso.it/viewer/{file_id}/{page}/page.css"
    page_css = requests.get(page_url).text
    page_css = replace_urls_to_absolute(url_viewer=url_viewer,
                                        content=page_css)

    html = template.render(file_id=file_id,
                           page=page,
                           content=content,
                           custom_css=custom_css,
                           base_css=base_css,
                           page_css=page_css)

    # Save html page
    file_name = f'{sanitize(file_id)}_{page:04}.html'
    with open(path + '/' + file_name, 'w', encoding='utf-8') as f:
        f.write(html)
    generate_pdf(path, file_name, landscape)


def generate_pdf(path: str, file_name: str, landscape: bool = False):
    """Generate PDF from html"""
    apath = os.path.abspath(path + '/' + file_name)
    out_name = file_name.split('.')[0] + '.pdf'

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        page = context.new_page()

        # Load local HTML
        file_url = f"file://{apath}"
        page.goto(file_url)

        # Generate PDF file
        page.pdf(path=f'{path}/{out_name}', format="A4", landscape=landscape)
        browser.close()


def join_pdf_pages(path: str, file_id: str, title: str, out_path: str):
    """Join all pdf pages in a single pdf file"""
    pdfs = [path + '/' + f for f in os.listdir(path) if f.endswith('.pdf')]
    pdfs.sort()

    merger = PdfWriter()
    for pdf in pdfs:
        merger.append(pdf)

    title = re.sub(r'[^\w\s]', '', title).replace(' ', '_')


    out_file_path = os.path.join(out_path, f'{sanitize(file_id)}_{title}.pdf')

    with open(out_file_path, "wb") as f:
        merger.write(f)
    return out_file_path


def process_page(domain: str, file_id: str, page: int, wpath: str,
                 landscape: bool, custom_css: str):
    """Download and process a single page"""
    content = get_html_page(domain, file_id, page)
    generate_page(domain, file_id, page, content, wpath, landscape, custom_css)
    return page

if __name__ == '__main__':
    # Create temp folder if not exists
    wpath = os.path.abspath(TEMP_FOLDER)
    create_folder_if_not_exists(wpath)

    # Enter url
    url = get_manual_url()

    # Get data from url
    try:
        domain = get_domain(url)
        print("Sto scaricando i dati del manuale...")
        pdf_data = get_data(url)
        file_id = pdf_data['file_id']
    except Exception as e:
        print(e)
        print('Errore: Dati del PDF non trovati')
        exit()

    # Ask continue downloading file
    print(f'{pdf_data["title"]} di {pdf_data["total_pages"]} pagine')
    continue_question = [{
        'type': 'confirm',
        'name': 'continue',
        'message': f'Continuo a scaricare il file?',
        'default': True
    }]
    continue_answer = prompt(continue_question)
    if not continue_answer.get('continue'):
        exit()

    # Create file_id folder
    wpath = wpath + f'/{sanitize(file_id)}'
    create_folder_if_not_exists(wpath)

    # Files in temp folder for skip already downloaded pages
    generated_files = [f for f in os.listdir(wpath) if f.endswith('.pdf')]

    # Ask for landscape
    landscape_question = [{
        'type': 'confirm',
        'name': 'landscape',
        'message': 'Modalità landscape?',
        'default': True
    }]
    landscape_answer = prompt(landscape_question)
    landscape = landscape_answer.get('landscape')
    
    for page in range(1, pdf_data['total_pages'] + 1):
        print(f"Estraendo la pagina {page}...")
        # If pdf page already exists, skip it
        if f'{sanitize(file_id)}_{page:04}.pdf' in generated_files:
            continue

        # Generate html page
        generate_page(domain,
                        file_id,
                        page,
                        get_html_page(domain, file_id, page),
                        wpath,
                        landscape,
                        custom_css=pdf_data["custom_css"])

    # Join all pdf pages in a single pdf file
    out_path = os.path.abspath('output')
    create_folder_if_not_exists(out_path)
    out_file = join_pdf_pages(wpath, file_id, pdf_data['title'], out_path)

    # Open pdf file
    # os.system(f'start "" "{out_file}"')
    os.startfile(out_file)

    # Delete temp folder
    shutil.rmtree(TEMP_FOLDER)