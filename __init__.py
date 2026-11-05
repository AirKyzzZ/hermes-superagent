# pytest imports this file with no parent package, which breaks the relative import.
if __package__:
    from .superagent.plugin import register

    __all__ = ["register"]
