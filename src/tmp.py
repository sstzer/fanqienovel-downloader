# -*- coding: utf-8 -*-
import requests as req
from lxml import etree
from ebooklib import epub
from tqdm import tqdm
import json, time, random, os
import concurrent.futures
from typing import Callable, Optional
from dataclasses import dataclass
from enum import Enum

import utils, cookie
import format


class SaveMode(Enum):
    SINGLE_TXT = 1
    SPLIT_TXT = 2
    EPUB = 3
    HTML = 4
    LATEX = 5

@dataclass
class Config:
    kg: int = 0
    kgf: str = '　'
    delay: list[int] = None
    save_path: str = ''
    save_mode: SaveMode = SaveMode.SINGLE_TXT
    space_mode: str = 'halfwidth'
    xc: int = 1

    def __post_init__(self):
        if self.delay is None:
            self.delay = [50, 150]

class NovelDownloader:
    def __init__(self,
                 config: Config,
                 progress_callback: Optional[Callable] = None,
                 log_callback: Optional[Callable] = None):
        self.config = config
        self.progress_callback = progress_callback or self._default_progress
        self.log_callback = log_callback or print

        # Initialize headers first
        self.headers_lib = [
            {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.63 Safari/537.36'},
            {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:91.0) Gecko/20100101 Firefox/91.0'},
            {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.63 Safari/537.36 Edg/93.0.961.47'}
        ]
        self.headers = random.choice(self.headers_lib)

        # Use absolute paths based on script location
        self.script_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_dir = os.path.join(self.script_dir, 'data')
        self.bookstore_dir = os.path.join(self.data_dir, 'bookstore')
        self.record_path = os.path.join(self.data_dir, 'record.json')
        self.config_path = os.path.join(self.data_dir, 'config.json')
        self.cookie_path = os.path.join(self.data_dir, 'cookie.json')

        self.CODE = [[58344, 58715], [58345, 58716]]

        # Load charset for text decoding
        charset_path = os.path.join(self.script_dir, 'charset.json')
        with open(charset_path, 'r', encoding='UTF-8') as f:
            self.charset = json.load(f)

        self._setup_directories()
        self.cookie=""
        cookie.init(self)

        # Add these variables
        self.zj = {}  # For storing chapter data
        self.cs = 0   # Chapter counter
        self.tcs = 0  # Test counter
        self.tzj = None  # Test chapter ID
        self.book_json_path = None  # Current book's JSON path

    def _setup_directories(self):
        """Create necessary directories if they don't exist"""
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.bookstore_dir, exist_ok=True)

    @dataclass
    class DownloadProgress:
        """Progress info for both CLI and web"""
        current: int
        total: int
        percentage: float
        description: str
        chapter_title: Optional[str] = None
        status: str = 'downloading'  # 'downloading', 'completed', 'error'
        error: Optional[str] = None

    def _default_progress(self, current: int, total: int, desc: str = '', chapter_title: str = None) -> DownloadProgress:
        """Progress tracking for both CLI and web"""
        # For CLI: Use tqdm directly
        if not hasattr(self, '_pbar'):
            self._pbar = tqdm(total=total, desc=desc)
        self._pbar.update(1)  # Update by 1 instead of setting n directly

        # For web: Return progress info
        return self.DownloadProgress(
            current=current,
            total=total,
            percentage=(current / total * 100) if total > 0 else 0,
            description=desc,
            chapter_title=chapter_title
        )

    def download_novel(self, novel_id: str) -> bool:
        """
        Download a novel by its ID
        Returns True if successful, False otherwise
        """
        try:
            novel_id = utils.parse_novel_id(self, novel_id)
            if not novel_id:
                return False

            utils.update_records(self.record_path, novel_id)

            if self.config.save_mode == SaveMode.EPUB:
                status = self._download_epub(novel_id)
            elif self.config.save_mode == SaveMode.HTML:
                status = self._download_html(novel_id)
            elif self.config.save_mode == SaveMode.LATEX:
                status = self._download_latex(novel_id)
            else:
                status = self._download_txt(novel_id)

            if status == 'err':
                self.log_callback('找不到此书')
                return False
            elif status == '已完结':
                self.log_callback('小说已完结')
                return True
            else:
                self.log_callback('下载完成')
                return True

        except Exception as e:
            self.log_callback(f'下载失败: {str(e)}')
            return False

    def search_novel(self, keyword: str) -> list[dict]:
        """
        Search for novels by keyword
        Returns list of novel info dictionaries
        """
        if not keyword:
            return []

        # Use the correct API endpoint from ref_main.py
        url = f"https://api5-normal-lf.fqnovel.com/reading/bookapi/search/page/v/"
        params = {
            "query": keyword,
            "aid": "1967",
            "channel": "0",
            "os_version": "0",
            "device_type": "0",
            "device_platform": "0",
            "iid": "466614321180296",
            "passback": "{(page-1)*10}",
            "version_code": "999"
        }

        try:
            response = req.get(url, params=params, headers=self.headers)
            response.raise_for_status()
            data = response.json()

            if data['code'] == 0 and data['data']:
                return data['data']
            else:
                self.log_callback("没有找到相关书籍。")
                return []

        except req.RequestException as e:
            self.log_callback(f"网络请求失败: {str(e)}")
            return []
        except json.JSONDecodeError as e:
            self.log_callback(f"解析搜索结果失败: {str(e)}")
            return []
        except Exception as e:
            self.log_callback(f'搜索失败: {str(e)}')
            return []

    # ... Additional helper methods would go here ...

    def _download_txt(self, novel_id: int) -> str:
        """Download novel in TXT format"""
        try:
            name, chapters, status = self._get_chapter_list(novel_id)
            if name == 'err':
                return 'err'

            safe_name = utils.sanitize_filename(name)
            self.log_callback(f'\n开始下载《{name}》，状态：{status[0]}')

            # Set book_json_path for the current download
            self.book_json_path = os.path.join(self.bookstore_dir, f'{safe_name}.json')

            # Initialize global variables for this download
            self.zj = {}
            self.cs = 0
            self.tcs = 0

            # Store metadata at the start
            metadata = {
                '_metadata': {
                    'novel_id': str(novel_id),  # Store as string to avoid JSON integer limits
                    'name': name,
                    'status': status[0] if status else None,
                    'last_updated': time.strftime('%Y-%m-%d %H:%M:%S')
                }
            }

            # Load existing content and merge with metadata
            existing_content = {}
            if os.path.exists(self.book_json_path):
                with open(self.book_json_path, 'r', encoding='UTF-8') as f:
                    existing_content = json.load(f)
                    # Keep existing chapters but update metadata
                    if isinstance(existing_content, dict):
                        existing_content.update(metadata)
            else:
                existing_content = metadata
                # Save initial metadata
                with open(self.book_json_path, 'w', encoding='UTF-8') as f:
                    json.dump(existing_content, f, ensure_ascii=False)

            total_chapters = len(chapters)
            completed_chapters = 0
            # Create CLI progress bar
            with tqdm(total=total_chapters, desc='下载进度') as pbar:
                # Download chapters
                content = existing_content.copy()  # Start with existing content including metadata
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.config.xc) as executor:
                    future_to_chapter = {
                        executor.submit(
                            self._download_chapter,
                            title,
                            chapter_id,
                            existing_content
                        ): title
                        for title, chapter_id in chapters.items()
                    }

                    for future in concurrent.futures.as_completed(future_to_chapter):
                        chapter_title = future_to_chapter[future]
                        try:
                            chapter_content = future.result()
                            if chapter_content:
                                content[chapter_title] = chapter_content
                                # Save progress periodically
                                if completed_chapters % 5 == 0:
                                    with open(self.book_json_path, 'w', encoding='UTF-8') as f:
                                        json.dump(content, f, ensure_ascii=False)
                        except Exception as e:
                            self.log_callback(f'下载章节失败 {chapter_title}: {str(e)}')

                        completed_chapters += 1
                        pbar.update(1)
                        self.progress_callback(
                            completed_chapters,
                            total_chapters,
                            '下载进度',
                            chapter_title
                        )

                # Save final content
                with open(self.book_json_path, 'w', encoding='UTF-8') as f:
                    json.dump(content, f, ensure_ascii=False)

                # Generate output file
                if self.config.save_mode == SaveMode.SINGLE_TXT:
                    return self._save_single_txt(safe_name, content)
                else:
                    return self._save_split_txt(safe_name, content)

        finally:
            # Send 100% completion if not already sent
            if 'completed_chapters' in locals() and 'total_chapters' in locals():
                if completed_chapters < total_chapters:
                    self.progress_callback(total_chapters, total_chapters, '下载完成')

    def _download_epub(self, novel_id: int) -> str:
        """Download novel in EPUB format"""
        try:
            name, chapters, status = self._get_chapter_list(novel_id)
            if name == 'err':
                return 'err'

            safe_name = utils.sanitize_filename(name)
            self.log_callback(f'\n开始下载《{name}》，状态：{status[0]}')

            # Create EPUB book
            book = epub.EpubBook()
            book.set_title(name)
            book.set_language('zh')

            # Get author info and cover
            if author:= utils.get_author_info(self, novel_id):
                book.add_author(author)
            if cover_url:= format.epub.get_cover_url(self, novel_id):
                format.epub.add_cover(self, book, cover_url)

            total_chapters = len(chapters)
            completed_chapters = 0

            # Download chapters with progress tracking
            epub_chapters = []
            with tqdm(total=total_chapters, desc='下载进度') as pbar:
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.config.xc) as executor:
                    future_to_chapter = {
                        executor.submit(
                            self._download_chapter_for_epub,
                            title,
                            chapter_id
                        ): title
                        for title, chapter_id in chapters.items()
                    }

                    for future in concurrent.futures.as_completed(future_to_chapter):
                        chapter_title = future_to_chapter[future]
                        try:
                            epub_chapter = future.result()
                            if epub_chapter:
                                epub_chapters.append(epub_chapter)
                                book.add_item(epub_chapter)
                        except Exception as e:
                            self.log_callback(f'下载章节失败 {chapter_title}: {str(e)}')

                        completed_chapters += 1
                        pbar.update(1)
                        self.progress_callback(
                            completed_chapters,
                            total_chapters,
                            '下载进度',
                            chapter_title
                        )

            # Add navigation
            book.toc = epub_chapters
            book.spine = ['nav'] + epub_chapters
            book.add_item(epub.EpubNcx())
            book.add_item(epub.EpubNav())

            # Save EPUB file
            epub_path = os.path.join(self.config.save_path, f'{safe_name}.epub')
            epub.write_epub(epub_path, book)
            return 's'

        finally:
            if 'completed_chapters' in locals() and 'total_chapters' in locals():
                if completed_chapters < total_chapters:
                    self.progress_callback(total_chapters, total_chapters, '下载完成')

    def _download_chapter(self, title: str, chapter_id: str, existing_content: dict) -> Optional[str]:
        """Download a single chapter with retries"""
        if title in existing_content:
            self.zj[title] = existing_content[title]  # Add this
            return existing_content[title]

        self.log_callback(f'下载章节: {title}')
        retries = 3
        last_error = None

        while retries > 0:
            try:
                content = self._download_chapter_content(chapter_id)
                if content == 'err':  # Add this check
                    raise Exception('Download failed')

                time.sleep(random.randint(
                    self.config.delay[0],
                    self.config.delay[1]
                ) / 1000)

                # Handle cookie refresh
                if content == 'err':
                    self.tcs += 1
                    if self.tcs > 7:
                        self.tcs = 0
                        cookie.get(self,self.tzj)
                    continue  # Try again with new cookie

                # Save progress periodically
                self.cs += 1
                if self.cs >= 5:
                    self.cs = 0
                    utils.save_progress(self, title, content)

                self.zj[title] = content  # Add this
                return content

            except Exception as e:
                last_error = e
                retries -= 1
                if retries == 0:
                    self.log_callback(f'下载失败 {title}: {str(e)}')
                    break
                time.sleep(1)

        if last_error:
            raise last_error
        return None

    def _download_chapter_for_epub(self, title: str, chapter_id: str) -> Optional[epub.EpubHtml]:
        """Download and format chapter for EPUB"""
        content = self._download_chapter(title, chapter_id, {})
        if not content:
            return None

        chapter = epub.EpubHtml(
            title=title,
            file_name=f'chapter_{chapter_id}.xhtml',
            lang='zh'
        )

        formatted_content = content.replace(
            '\n',
            f'\n{self.config.kgf * self.config.kg}'
        )
        chapter.content = f'<h1>{title}</h1><p>{formatted_content}</p>'
        return chapter

    def _save_single_txt(self, name: str, content: dict) -> str:
        """Save all chapters to a single TXT file"""
        output_path = os.path.join(self.config.save_path, f'{name}.txt')
        fg = '\n' + self.config.kgf * self.config.kg

        with open(output_path, 'w', encoding='UTF-8') as f:
            for title, chapter_content in content.items():
                f.write(f'\n{title}{fg}')
                if self.config.kg == 0:
                    f.write(f'{chapter_content}\n')
                else:
                    f.write(f'{chapter_content.replace("\n", fg)}\n')
        return 's'

    def _save_split_txt(self, name: str, content: dict) -> str:
        """Save each chapter to a separate TXT file"""
        output_dir = os.path.join(self.config.save_path, name)
        os.makedirs(output_dir, exist_ok=True)

        for title, chapter_content in content.items():
            chapter_path = os.path.join(
                output_dir,
                f'{utils.sanitize_filename(title)}.txt'
            )
            with open(chapter_path, 'w', encoding='UTF-8') as f:
                if self.config.kg == 0:
                    f.write(f'{chapter_content}\n')
                else:
                    f.write(
                        f'{chapter_content.replace("\n", self.config.kgf * self.config.kg)}\n'
                    )
        return 's'

    def _download_html(self, novel_id: int) -> str:
        """Download novel in HTML format"""
        try:
            name, chapters, status = self._get_chapter_list(novel_id)
            if name == 'err':
                return 'err'

            safe_name = utils.sanitize_filename(name)
            html_dir = os.path.join(self.config.save_path, f"{safe_name}(html)")
            os.makedirs(html_dir, exist_ok=True)

            self.log_callback(f'\n开始下载《{name}》，状态：{status[0]}')

            # Create index.html
            toc_content = format.html.index(name, chapters)
            with open(os.path.join(html_dir, "index.html"), "w", encoding='UTF-8') as f:
                f.write(toc_content)

            total_chapters = len(chapters)
            completed_chapters = 0

            # Download chapters with progress tracking
            with tqdm(total=total_chapters, desc='下载进度') as pbar:
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.config.xc) as executor:
                    future_to_chapter = {
                        executor.submit(
                            self._download_chapter_for_html,
                            title,
                            chapter_id,
                            html_dir,
                            list(chapters.keys())
                        ): title
                        for title, chapter_id in chapters.items()
                    }

                    for future in concurrent.futures.as_completed(future_to_chapter):
                        chapter_title = future_to_chapter[future]
                        try:
                            future.result()
                        except Exception as e:
                            self.log_callback(f'下载章节失败 {chapter_title}: {str(e)}')

                        completed_chapters += 1
                        pbar.update(1)
                        self.progress_callback(
                            completed_chapters,
                            total_chapters,
                            '下载进度',
                            chapter_title
                        )

            return 's'

        finally:
            if 'completed_chapters' in locals() and 'total_chapters' in locals():
                if completed_chapters < total_chapters:
                    self.progress_callback(total_chapters, total_chapters, '下载完成')

    def _download_latex(self, novel_id: int) -> str:
        """Download novel in LaTeX format"""
        try:
            name, chapters, status = self._get_chapter_list(novel_id)
            if name == 'err':
                return 'err'

            safe_name = utils.sanitize_filename(name)
            self.log_callback(f'\n开始下载《{name}》，状态：{status[0]}')

            # Create LaTeX document header
            latex_content = format.latex.header(name)

            total_chapters = len(chapters)
            completed_chapters = 0
            chapter_contents = []

            # Download chapters with progress tracking
            with tqdm(total=total_chapters, desc='下载进度') as pbar:
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.config.xc) as executor:
                    future_to_chapter = {
                        executor.submit(
                            self._download_chapter_for_latex,
                            title,
                            chapter_id
                        ): title
                        for title, chapter_id in chapters.items()
                    }

                    for future in concurrent.futures.as_completed(future_to_chapter):
                        chapter_title = future_to_chapter[future]
                        try:
                            chapter_content = future.result()
                            if chapter_content:
                                chapter_contents.append((chapter_title, chapter_content))
                        except Exception as e:
                            self.log_callback(f'下载章节失败 {chapter_title}: {str(e)}')

                        completed_chapters += 1
                        pbar.update(1)
                        self.progress_callback(
                            completed_chapters,
                            total_chapters,
                            '下载进度',
                            chapter_title
                        )

            # Sort chapters and add to document
            chapter_contents.sort(key=lambda x: list(chapters.keys()).index(x[0]))
            for title, content in chapter_contents:
                latex_content += format.latex.chapter(title, content, self.config.kgf * self.config.kg)

            # Add document footer and save
            latex_content += "\n\\end{document}"
            latex_path = os.path.join(self.config.save_path, f'{safe_name}.tex')
            with open(latex_path, 'w', encoding='UTF-8') as f:
                f.write(latex_content)

            return 's'

        finally:
            if 'completed_chapters' in locals() and 'total_chapters' in locals():
                if completed_chapters < total_chapters:
                    self.progress_callback(total_chapters, total_chapters, '下载完成')

    def _download_chapter_for_html(self, title: str, chapter_id: str, output_dir: str, all_titles: list[str]) -> None:
        """Download and format chapter for HTML"""
        content = self._download_chapter(title, chapter_id, {})
        if not content:
            return

        current_index = all_titles.index(title)
        prev_link = f'<a href="{utils.sanitize_filename(all_titles[current_index-1])}.html">上一章</a>' if current_index > 0 else ''
        next_link = f'<a href="{utils.sanitize_filename(all_titles[current_index+1])}.html">下一章</a>' if current_index < len(all_titles)-1 else ''

        html_content = format.html.content(title, content, prev_link, next_link, self.config.kgf * self.config.kg)

        with open(os.path.join(output_dir, f"{utils.sanitize_filename(title)}.html"), "w", encoding='UTF-8') as f:
            f.write(html_content)

    def _download_chapter_for_latex(self, title: str, chapter_id: str) -> Optional[str]:
        """Download and format chapter for LaTeX"""
        content = self._download_chapter(title, chapter_id, {})
        if not content:
            return None
        return format.latex.chapter(title, content, self.config.kgf * self.config.kg)

    def _get_chapter_list(self, novel_id: int) -> tuple:
        """Get novel info and chapter list"""
        url = f'https://fanqienovel.com/page/{novel_id}'
        response = req.get(url, headers=self.headers)
        ele = etree.HTML(response.text)

        chapters = {}
        a_elements = ele.xpath('//div[@class="chapter"]/div/a')
        if not a_elements:  # Add this check
            return 'err', {}, []

        for a in a_elements:
            href = a.xpath('@href')
            if not href:  # Add this check
                continue
            chapters[a.text] = href[0].split('/')[-1]

        title = ele.xpath('//h1/text()')
        status = ele.xpath('//span[@class="info-label-yellow"]/text()')

        if not title or not status:  # Check both title and status
            return 'err', {}, []

        return title[0], chapters, status

    def _download_chapter_content(self, chapter_id: int, test_mode: bool = False) -> str:
        """Download content with fallback and better error handling"""
        headers = self.headers.copy()
        headers['cookie'] = self.cookie

        for attempt in range(3):
            try:
                # Try primary method
                response = req.get(
                    f'https://fanqienovel.com/reader/{chapter_id}',
                    headers=headers,
                    timeout=10
                )
                response.raise_for_status()

                content = '\n'.join(
                    etree.HTML(response.text).xpath(
                        '//div[@class="muye-reader-content noselect"]//p/text()'
                    )
                )

                if test_mode:
                    return content

                try:
                    return utils.decode_content(self, content)
                except:
                    # Try alternative decoding mode
                    try:
                        return utils.decode_content(self, content, mode=1)
                    except:
                        # Fallback HTML processing
                        content = content[6:]
                        tmp = 1
                        result = ''
                        for i in content:
                            if i == '<':
                                tmp += 1
                            elif i == '>':
                                tmp -= 1
                            elif tmp == 0:
                                result += i
                            elif tmp == 1 and i == 'p':
                                result = (result + '\n').replace('\n\n', '\n')
                        return result

            except Exception as e:
                # Try alternative API endpoint
                try:
                    response = req.get(
                        f'https://fanqienovel.com/api/reader/full?itemId={chapter_id}',
                        headers=headers
                    )
                    content = json.loads(response.text)['data']['chapterData']['content']

                    if test_mode:
                        return content

                    return utils.decode_content(self, content)
                except:
                    if attempt == 2:  # Last attempt
                        if test_mode:
                            return 'err'
                        raise Exception(f"Download failed after 3 attempts: {str(e)}")
                    time.sleep(1)



    def get_downloaded_novels(self) -> list[dict[str, str]]:
        """Get list of downloaded novels with their paths"""
        novels = []
        for filename in os.listdir(self.bookstore_dir):
            if filename.endswith('.json'):
                novel_name = filename[:-5]  # Remove .json extension
                json_path = os.path.join(self.bookstore_dir, filename)

                try:
                    with open(json_path, 'r', encoding='UTF-8') as f:
                        novel_data = json.load(f)
                        metadata = novel_data.get('_metadata', {})

                        novels.append({
                            'name': novel_name,
                            'novel_id': metadata.get('novel_id'),
                            'status': metadata.get('status'),
                            'last_updated': metadata.get('last_updated'),
                            'json_path': json_path,
                            'txt_path': os.path.join(self.config.save_path, f'{novel_name}.txt'),
                            'epub_path': os.path.join(self.config.save_path, f'{novel_name}.epub'),
                            'html_path': os.path.join(self.config.save_path, f'{novel_name}(html)'),
                            'latex_path': os.path.join(self.config.save_path, f'{novel_name}.tex')
                        })
                except Exception as e:
                    self.log_callback(f"Error reading novel data for {novel_name}: {str(e)}")
                    # Add novel with minimal info if metadata can't be read
                    novels.append({
                        'name': novel_name,
                        'novel_id': None,
                        'status': None,
                        'last_updated': None,
                        'json_path': json_path,
                        'txt_path': os.path.join(self.config.save_path, f'{novel_name}.txt'),
                        'epub_path': os.path.join(self.config.save_path, f'{novel_name}.epub'),
                        'html_path': os.path.join(self.config.save_path, f'{novel_name}(html)'),
                        'latex_path': os.path.join(self.config.save_path, f'{novel_name}.tex')
                    })
        return novels

