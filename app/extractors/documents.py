import asyncio
import os
import zipfile
from typing import Optional
from app.core.config import settings
from app.core.logger import log
from app.db.models import ContentType
from app.extractors.base import BaseExtractor, ExtractedContent


class DocumentExtractor(BaseExtractor):
    """
    Extracts text from PDF, DOCX, TXT, and Markdown files safely using asyncio threads.
    """
    async def extract(self, source: str, **kwargs) -> ExtractedContent:
        file_path = source
        filename = kwargs.get("filename") or os.path.basename(file_path)
        ext = os.path.splitext(filename)[1].lower()

        # Check file size
        try:
            size_mb = os.path.getsize(file_path) / (1024 * 1024)
            if size_mb > settings.MAX_FILE_SIZE_MB:
                return ExtractedContent(
                    title=filename,
                    author=None,
                    date=None,
                    text="",
                    language=None,
                    domain="file",
                    content_type=ContentType.DOCUMENT,
                    url=None,
                    success=False,
                    error_message=f"Размер файла ({size_mb:.1f} МБ) превышает лимит {settings.MAX_FILE_SIZE_MB} МБ.",
                )
        except OSError as e:
            return ExtractedContent(
                title=filename,
                author=None,
                date=None,
                text="",
                language=None,
                domain="file",
                content_type=ContentType.DOCUMENT,
                url=None,
                success=False,
                error_message=f"Ошибка чтения файла: {e}",
            )

        try:
            text, title, author = await asyncio.to_thread(self._extract_sync, file_path, ext, filename)
        except Exception as e:
            log.exception(f"Error parsing document {file_path}: {e}")
            return ExtractedContent(
                title=filename,
                author=None,
                date=None,
                text="",
                language=None,
                domain="file",
                content_type=ContentType.DOCUMENT,
                url=None,
                success=False,
                error_message=f"Ошибка парсинга документа: {str(e)[:100]}",
            )

        if len(text) > settings.MAX_TEXT_LENGTH:
            text = text[:settings.MAX_TEXT_LENGTH] + "\n\n... [текст документа обрезан]"

        has_text = bool(text and len(text.strip()) >= 20)
        return ExtractedContent(
            title=title,
            author=author,
            date=None,
            text=text if has_text else "",
            language=None,
            domain="file",
            content_type=ContentType.DOCUMENT,
            url=None,
            success=has_text,
            error_message=None if has_text else "Не удалось извлечь печатный текст из файла (возможно, скан без OCR).",
        )

    def _extract_sync(self, file_path: str, ext: str, filename: str) -> tuple[str, str, Optional[str]]:
        text = ""
        author = None
        title = filename

        if ext == ".pdf":
            text, pdf_title, pdf_author = self._extract_pdf(file_path)
            if pdf_title:
                title = f"{pdf_title} ({filename})"
            if pdf_author:
                author = pdf_author

        elif ext == ".docx":
            text, doc_author = self._extract_docx(file_path)
            if doc_author:
                author = doc_author

        elif ext in (".txt", ".md", ".markdown", ".rst"):
            text = self._extract_text(file_path)

        elif ext == ".zip":
            text, zip_title, zip_author = self._extract_zip(file_path, filename)
            if zip_title:
                title = zip_title
            if zip_author:
                author = zip_author

        else:
            raise ValueError(f"Неподдерживаемый формат файла: {ext}")

        return text, title, author

    def _extract_zip(self, file_path: str, filename: str) -> tuple[str, str, Optional[str]]:
        if not zipfile.is_zipfile(file_path):
            raise ValueError("Файл не является корректным ZIP-архивом.")

        text_exts = {
            ".txt", ".md", ".markdown", ".rst",
            ".py", ".js", ".ts", ".jsx", ".tsx",
            ".html", ".htm", ".css", ".scss",
            ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
            ".csv", ".tsv", ".sql", ".sh", ".bash", ".bat", ".ps1",
            ".c", ".cpp", ".h", ".hpp", ".rs", ".go", ".java", ".kt",
            ".xml", ".log",
        }
        named_text_files = {"dockerfile", "makefile", "license", "readme"}

        manifest_lines = []
        text_sections = []
        total_extracted_len = 0
        title = f"Архив {filename}"
        author = None

        with zipfile.ZipFile(file_path, "r") as zf:
            infolist = zf.infolist()
            # Отфильтровываем служебные файлы (macOS, скрытые файлы, пути с ..)
            files = [
                info for info in infolist
                if not info.is_dir()
                and not info.filename.startswith("__MACOSX/")
                and not os.path.basename(info.filename).startswith(".")
                and ".." not in info.filename
            ]

            manifest_lines.append(f"📦 Содержимое архива «{filename}» ({len(files)} файлов):")
            for info in files[:50]:
                size_kb = info.file_size / 1024
                manifest_lines.append(f"• {info.filename} ({size_kb:.1f} КБ)")
            if len(files) > 50:
                manifest_lines.append(f"... и ещё {len(files) - 50} файлов")

            # Приоритет отдаём README и markdown файлам
            def _sort_key(info):
                bname = os.path.basename(info.filename).lower()
                if "readme" in bname:
                    return 0
                if bname.endswith((".md", ".txt")):
                    return 1
                return 2

            for info in sorted(files, key=_sort_key):
                if total_extracted_len >= settings.MAX_TEXT_LENGTH:
                    break

                bname = os.path.basename(info.filename).lower()
                f_ext = os.path.splitext(bname)[1].lower()

                if f_ext in text_exts or bname in named_text_files:
                    # Пропускаем гигантские файлы распаковки (> 1 МБ)
                    if info.file_size > 1_048_576:
                        continue
                    try:
                        raw = zf.read(info.filename)
                        content_str = None
                        for enc in ("utf-8", "cp1251", "latin-1"):
                            try:
                                content_str = raw[:40000].decode(enc).strip()
                                break
                            except UnicodeDecodeError:
                                continue
                        if content_str:
                            section = f"📄 Файл: {info.filename}\n{content_str}"
                            text_sections.append(section)
                            total_extracted_len += len(section)
                            if "readme" in bname and not author:
                                first_line = content_str.split("\n")[0].strip("# \t")
                                if first_line and len(first_line) < 80:
                                    title = f"{first_line} ({filename})"
                    except Exception as fe:
                        log.debug(f"Failed reading {info.filename} from zip: {fe}")
                        continue

        full_text = "\n\n".join(manifest_lines)
        if text_sections:
            full_text += "\n\n" + "\n\n---\n\n".join(text_sections)

        return full_text, title, author

        return text, title, author

    def _extract_pdf(self, file_path: str) -> tuple[str, Optional[str], Optional[str]]:
        text_parts = []
        title = None
        author = None

        try:
            import fitz  # PyMuPDF
            with fitz.open(file_path) as doc:
                meta = doc.metadata or {}
                title = meta.get("title") or None
                author = meta.get("author") or None
                for idx, page in enumerate(doc):
                    if idx >= 150:  # Max 150 pages
                        break
                    text_parts.append(page.get_text())
                    if sum(len(p) for p in text_parts) > settings.MAX_TEXT_LENGTH:
                        break
            return "\n\n".join(text_parts).strip(), title, author
        except ImportError:
            pass

        # Fallback to pypdf
        import pypdf
        with open(file_path, "rb") as f:
            reader = pypdf.PdfReader(f)
            if reader.metadata:
                title = reader.metadata.title
                author = reader.metadata.author
            for idx, page in enumerate(reader.pages):
                if idx >= 150:
                    break
                extracted = page.extract_text()
                if extracted:
                    text_parts.append(extracted)
                if sum(len(p) for p in text_parts) > settings.MAX_TEXT_LENGTH:
                    break

        return "\n\n".join(text_parts).strip(), title, author

    def _extract_docx(self, file_path: str) -> tuple[str, Optional[str]]:
        import docx
        doc = docx.Document(file_path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        author = None
        try:
            core_props = doc.core_properties
            author = core_props.author or None
        except Exception:
            pass
        return "\n\n".join(paragraphs).strip(), author

    def _extract_text(self, file_path: str) -> str:
        for enc in ("utf-8", "cp1251", "latin-1"):
            try:
                with open(file_path, "r", encoding=enc) as f:
                    return f.read().strip()
            except UnicodeDecodeError:
                continue
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
