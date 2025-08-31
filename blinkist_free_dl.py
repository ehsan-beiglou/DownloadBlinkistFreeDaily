import cloudscraper
from datetime import datetime
from pathlib import Path
from pathvalidate import sanitize_filepath
from rich.console import Console
from rich.progress import track
from mutagen.mp4 import MP4
import tenacity

BASE_URL = 'https://www.blinkist.com/'

HEADERS = {
    'x-requested-with': 'XMLHttpRequest',
}

CLOUDFLARE_MAX_ATTEMPTS = 3
CLOUDFLARE_WAIT_TIME = 2

LOCALES = ['en']
DOWNLOAD_DIR = Path('C:/Downloads')

console = Console()
scraper = cloudscraper.create_scraper()

@tenacity.retry(
    retry=tenacity.retry_if_exception_type(cloudscraper.exceptions.CloudflareChallengeError),
    wait=tenacity.wait_fixed(CLOUDFLARE_WAIT_TIME),
    stop=tenacity.stop_after_attempt(CLOUDFLARE_MAX_ATTEMPTS),
    before_sleep=lambda retry_state: console.print(f"Retrying in {retry_state.next_action.sleep} seconds..."),
)

def _request(url, **kwargs):
    # Wrapper for verifying and retrying GET requests.
    kwargs.setdefault('headers', HEADERS)
    response = scraper.get(url, **kwargs)

    # TODO: handle Cloudflare errors
    # We don't check the reponse content here; it could be large binary data and slow things down.
    # if response.status_code == 403:
    #     # TODO: reset scraper for the next try?
    #     raise cloudscraper.exceptions.CloudflareChallengeError()

    response.raise_for_status()  # handle other errors
    return response


def _api_request(endpoint, params=None):
    """
    Wrapper for verifying and retrying GET requests to the Blinkist API.
    Returns the parsed JSON response.
    Calls `_request` internally.
    """
    url = f"{BASE_URL}api/{endpoint}"
    response = _request(url, params=params, headers=HEADERS)
    return response.json()


def get_book_dir(book):
    return DOWNLOAD_DIR / sanitize_filepath(f"{datetime.today().strftime('%Y-%m-%d')} - {book['title']}")


def get_free_daily(locale='en'):
    # see also: https://www.blinkist.com/en/content/daily
    return _api_request('free_daily', params={'locale': locale})


def get_chapters(book_slug):
    return _api_request(f'books/{book_slug}/chapters')['chapters']


def get_chapter(book_id, chapter_id):
    return _api_request(f'books/{book_id}/chapters/{chapter_id}')


def download_book_text(book, chapters):
    console.print(f"Saving book text...")
    file_path = book_dir / sanitize_filepath(f"{book['title']}.md")

    if file_path.exists():
        console.print(f"Skipping existing file: {file_path}")
        return
    
    text = create_markdown_text(book, chapters)
    with open(file_path, mode="w+", encoding="utf-8") as file:
        file.write(text)


def download_chapter_audio(book, chapter_data):
    file_path = book_dir / f"0{chapter_data['order_no']}.m4a"
    
    if file_path.exists():
        console.print(f"Skipping existing file: {file_path}")
        return

    assert 'm4a' in chapter_data['signed_audio_url']
    console.print(f"\nDownloading audio file for: 0{chapter['order_no']} - {chapter['action_title']}")
    response = _request(chapter_data['signed_audio_url'])
    assert response.status_code == 200
    file_path.write_bytes(response.content)
    set_m4a_meta_data(
        filename = file_path,
        artist = book["author"],
        title = chapter_data['action_title'],
        album = book["title"]
    )


def download_book_cover(book):
    # find the URL of the largest version
    urls = set()
    for source in book['image']['sources']:
        urls.add(source['src'])
        urls |= set(source['srcset'].values())
    url = sorted(urls, key=lambda x: int(x.split('/')[-1].rstrip('.jpg')), reverse=True)[0]

    file_path = book_dir / "cover.jpg"

    if file_path.exists():
        console.print(f"Skipping existing file: {file_path}")
        return

    assert url.endswith('.jpg')
    response = _request(url)
    assert response.status_code == 200
    file_path.write_bytes(response.content)


def create_markdown_text(book, chapters):
    markdown_text = f"# {book['title']}\n\n"
    markdown_text += f"_{book['author']}_\n\n"

    for chapter in chapters:
        markdown_text += f"## Blink 0{chapter['order_no']} - {chapter['action_title']}\n\n"
        markdown_text += f"{chapter['text']}\n\n"

    markdown_text += f"Source: {book['url']}\n\n"
    return markdown_text


def set_m4a_meta_data(
    filename,
    artist=None,
    title=None,
    album=None
):
    mp4_file = MP4(filename)

    if not mp4_file.tags:
        mp4_file.add_tags()

    tags = mp4_file.tags

    if artist:
        tags["\xa9ART"] = artist
    if title:
        tags["\xa9alb"] = album
    if album:
        tags["\xa9nam"] = title
    tags.save(filename)


with console.status(f"Retrieving free daily ..."):
    free_daily = get_free_daily()

book = free_daily['book']
book_dir = get_book_dir(book)
book_dir.mkdir(exist_ok=True)

console.print(f"Today's free daily is: “{book['title']}”")

# list of chapters without their content
with console.status(f"Retrieving chapters of {book['title']}..."):
    chapter_list = get_chapters(book['slug'])

# fetch chapter content
chapters = [get_chapter(book['id'], chapter['id'])
            for chapter in track(chapter_list, description='Fetching chapters...')]
print("\n")
for chapter in chapters:
    console.print(f"0{chapter['order_no']} - {chapter['action_title']}")
print("\n")

# download text
with console.status(f"Downloading book text..."):
    download_book_text(book, chapters)

# download audio
for chapter in track(chapters, description='Downloading audio...'):
    download_chapter_audio(book, chapter)

# download cover
with console.status(f"Downloading cover..."):
    download_book_cover(book)