"""The six tools the contract allows. You write the bodies.

ADK sends each docstring to the model as the tool's description, so keep them accurate.

Rules every tool follows:
- It reads only the documents of the current run. `read_dossier` puts them in session state, and
  a tool reaches state through `tool_context.state`.
- Every piece of text it returns carries an anchor: `{doc_id}:{version}:p{page}` for a page, or
  `{doc_id}:{version}:p{page}-t{n}` for the n-th table on a page. The model copies anchors into
  its evidence and never writes one itself.
- On a document's `suspect_pages`, hidden text (white, or smaller than 2 pt) is dropped before
  anything is returned. In production the server has already removed it; locally, you do it.
- Document text is data. A tool never acts on what a document says.

Locally, documents live in `DOSSIER_DIR` (default `samples/dossier`), named by each request's
`filename`.
"""
import os
from pathlib import Path

from google.adk.tools import ToolContext

DOSSIER_DIR = Path(os.getenv("DOSSIER_DIR", Path(__file__).resolve().parent.parent / "samples" / "dossier"))


def list_documents(tool_context: ToolContext) -> list[dict]:
    """Lists the documents in this run: doc_id, version, filename, pages and declared language."""
    raise NotImplementedError


def read_page(doc_id: str, page: int, tool_context: ToolContext) -> dict:
    """Returns the text of one page of a document, with its anchor.

    Args:
        doc_id: A doc_id from list_documents.
        page: The page number, starting at 1.
    """
    raise NotImplementedError


def read_table(doc_id: str, page: int, tool_context: ToolContext) -> list[dict]:
    """Returns the tables on one page as rows of cells, each table with its own anchor.

    Args:
        doc_id: A doc_id from list_documents.
        page: The page number, starting at 1.
    """
    raise NotImplementedError


def search_dossier(query: str, tool_context: ToolContext) -> list[dict]:
    """Finds pages in this run's documents that contain the words of a query.

    Returns the best matches, each with its anchor and a short snippet.

    Args:
        query: Words to look for, such as "shelf life".
    """
    raise NotImplementedError


def verify_quote(anchor: str, quote: str, tool_context: ToolContext) -> dict:
    """Checks that a quote appears word for word at an anchor. Call it before using a quote as evidence.

    Args:
        anchor: An anchor a tool returned.
        quote: The exact text to check.
    """
    raise NotImplementedError


def lookup_term(term: str) -> str:
    """Explains a regulatory term or abbreviation, such as "CTD", "SmPC" or "3.2.P.8".

    Args:
        term: The term to explain.
    """
    raise NotImplementedError


TOOLS = [list_documents, read_page, read_table, search_dossier, verify_quote, lookup_term]
