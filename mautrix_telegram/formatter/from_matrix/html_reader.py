try:
    from .html_reader_lxml import HTMLNode, read_html
except ImportError:
    from .html_reader_htmlparser import HTMLNode, read_html
