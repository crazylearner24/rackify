try:
    from .rackify_app import main
except ImportError:
    from rackify_app import main

if __name__ == "__main__":
    import flet as ft

    ft.run(main,assets_dir="assets")