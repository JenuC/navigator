"""Entry point."""

from imgui_bundle import immapp

from .app import App


def main() -> None:
    app = App()
    immapp.run(
        gui_function=app.render,
        window_title="Stage Navigator",
        window_size=(1400, 900),
        fps_idle=60,
    )


if __name__ == "__main__":
    main()
