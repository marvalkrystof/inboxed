from textual.app import ComposeResult
from textual.containers import Center, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static


class HomeScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        with Center(), Vertical(id="home-menu"):
            yield Static("Inboxed", id="home-title")
            yield Static("Local email classification and review", id="home-subtitle")
            yield Button("Inbox and review", id="home-inbox", variant="primary")
            yield Button("Manage runs", id="home-runs")
            yield Button("Manage accounts", id="home-accounts")
            yield Button("Settings", id="home-settings")
            yield Button("Quit", id="home-quit")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "home-inbox":
            self.app.show_workspace()
        elif event.button.id == "home-runs":
            self.app.show_runs()
        elif event.button.id == "home-accounts":
            self.app.show_accounts()
        elif event.button.id == "home-settings":
            self.app.show_settings()
        elif event.button.id == "home-quit":
            self.app.exit()
