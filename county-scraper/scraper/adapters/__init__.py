from . import civicclerk, civicplus, html_docs, municode, wordpress

REGISTRY = {
    "civicplus": civicplus.fetch,
    "civicclerk": civicclerk.fetch,
    "wordpress": wordpress.fetch,
    "municode": municode.fetch,
    "html_docs": html_docs.fetch,
}
