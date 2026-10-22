if __name__ == "__main__" and not __package__:
    from _cli_bootstrap import prepare_direct_run

    __package__ = prepare_direct_run(__file__)

from .cli.sales import main

raise SystemExit(main())
