from pathlib import Path
from threading import Event
from typing import ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, TabbedContent, TabPane

from inboxed.config import load_settings
from inboxed.domain.errors import InboxedError

from .controller import TUIController
from .messages import JobFinished, JobProgress, JobStarted
from .screens.accounts import AccountEditScreen, AccountsScreen
from .screens.classes import ClassesScreen
from .screens.dialog import QuitScreen
from .screens.encryption import RunPasswordScreen
from .screens.runs import RunsScreen
from .screens.settings import SettingsScreen
from .screens.unsubscribe import UnsubscribeScreen
from .screens.workspace import WorkspaceScreen


class InboxedApp(App):
    TITLE = "Inboxed"
    SUB_TITLE = "Classify · Review · Apply · Unsubscribe"
    CSS_PATH = "styles.tcss"

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("1", "switch_tab('workspace')", "Workspace"),
        Binding("2", "switch_tab('runs')", "Runs"),
        Binding("3", "switch_tab('unsubscribe')", "Unsubscribe"),
        Binding("4", "switch_tab('accounts')", "Accounts"),
        Binding("5", "switch_tab('classes')", "Classes"),
        Binding("6", "switch_tab('settings')", "Settings"),
        Binding("q", "quit", "Quit"),
        Binding("ctrl+q", "quit", "Quit", show=False, priority=True),
    ]

    def __init__(self, controller: TUIController, config_path: Path | None = None):
        super().__init__()
        self.controller = controller
        self.config_path = config_path
        self.quit_after_job = False
        self._job_counter = 0
        # Map of job_id -> {"stop_event": Event, "kind": str, "label": str}
        self._jobs: dict[int, dict] = {}
        self.workspace = WorkspaceScreen(controller)
        self.runs = RunsScreen(controller.repository)
        self.accounts = AccountsScreen(controller, config_path)
        self.classes_screen = ClassesScreen(controller, config_path)
        self.settings_screen = SettingsScreen(controller, config_path)
        self.unsubscribe_screen = UnsubscribeScreen(controller)

    def compose(self) -> ComposeResult:
        with TabbedContent(initial="workspace", id="main-tabs"):
            with TabPane("Workspace", id="workspace"):
                yield self.workspace
            with TabPane("Runs", id="runs"):
                yield self.runs
            with TabPane("Unsubscribe", id="unsubscribe"):
                yield self.unsubscribe_screen
            with TabPane("Accounts", id="accounts"):
                yield self.accounts
            with TabPane("Classes", id="classes"):
                yield self.classes_screen
            with TabPane("Settings", id="settings"):
                yield self.settings_screen
        yield Footer()

    def on_mount(self):
        if self.controller.repository.locked:
            self.push_screen(
                RunPasswordScreen(self.controller.repository, "unlock"), self._history_unlocked
            )

    def _history_unlocked(self, success):
        if not success:
            self.exit()
            return
        self.workspace.refresh_runs(open_latest=bool(self.workspace.account))
        self.runs.refresh_rows()

    async def action_quit(self):
        if isinstance(self.screen, RunPasswordScreen) and self.screen.busy:
            self.notify("Wait for the encryption operation to finish before quitting")
            return
        if isinstance(self.screen, AccountEditScreen) and self.screen.busy:
            self.notify("Wait for account verification to finish before quitting")
            return
        if isinstance(self.screen, QuitScreen):
            return
        if self._jobs:

            def chosen(stop):
                if stop:
                    self.quit_after_job = True
                    self.request_stop()
                    if not self._jobs:
                        self.exit()

            self.push_screen(QuitScreen(), chosen)
        else:
            self.exit()

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated):
        if event.pane.id == "settings":
            self.settings_screen.discover_models()

    def action_switch_tab(self, tab: str) -> None:
        tabs = self.query_one("#main-tabs", TabbedContent)
        tabs.active = tab

    def _tab_button_id(self, tab: str) -> str:
        """Return the DOM id of the tab button for a given tab pane id."""
        return f"--content-tab-{tab}"

    def show_home(self):
        """Switch to Workspace tab (replaces the old home-screen navigate-back)."""
        self.action_switch_tab("workspace")

    def show_runs(self):
        self.action_switch_tab("runs")

    def show_accounts(self):
        self.action_switch_tab("accounts")

    def show_settings(self):
        self.action_switch_tab("settings")

    def show_classes(self):
        self.action_switch_tab("classes")

    def show_workspace(self, account_alias=None, run_id=None):
        """Switch to Workspace tab, optionally loading a specific account/run."""
        ws = self.workspace
        if account_alias is not None and account_alias in self.controller.settings.accounts:
            try:
                ws.set_account(account_alias)
            except InboxedError:
                pass
        if run_id is not None:
            ws.requested_run_id = run_id
            self.call_after_refresh(lambda: ws.open_run(run_id))
        self.action_switch_tab("workspace")

    # ------------------------------------------------------------------ jobs

    def start_job(self, kind: str, operation, context: dict, label: str = "") -> int:
        """Launch a background job and return its job_id."""
        self._job_counter += 1
        job_id = self._job_counter
        stop_event = Event()
        self._jobs[job_id] = {
            "stop_event": stop_event,
            "kind": kind,
            "label": label or kind,
            "run_id": context.get("run_id") if context else None,
        }
        run_id = (context or {}).get("run_id")
        self.post_message(JobStarted(job_id, kind, label or kind, run_id=run_id))
        self._run_job(job_id, kind, operation, context, stop_event)
        return job_id

    @work(thread=True, exit_on_error=False)
    def _run_job(self, job_id: int, kind: str, operation, context: dict, stop_event: Event):
        def progress(payload):
            self.post_message(JobProgress(job_id, payload))

        try:
            result = operation(progress, stop_event.is_set)
            self.post_message(JobFinished(job_id, kind, result=result, context=context))
        except Exception as exc:  # noqa: BLE001
            self.post_message(JobFinished(job_id, kind, error=str(exc), context=context))

    def request_stop(self, job_id: int | None = None) -> None:
        """Stop a specific job, or the most-recent job if job_id is None."""
        if job_id is None:
            if self._jobs:
                job_id = max(self._jobs)
            else:
                return
        job = self._jobs.get(job_id)
        if job:
            job["stop_event"].set()
            self.workspace.log_activity(
                f"Stop requested for job #{job_id} ({job['kind']}); "
                "waiting for current operation to finish",
                "WARN",
                run_id=job.get("run_id"),
            )

    def stop_event_for(self, job_id: int) -> Event | None:
        job = self._jobs.get(job_id)
        return job["stop_event"] if job else None

    def is_job_stopping(self, job_id: int) -> bool:
        job = self._jobs.get(job_id)
        return bool(job and job["stop_event"].is_set())

    def stop_run(self, run_id: str) -> bool:
        """Cancel any active background job for the given run_id."""
        stopped = False
        for job_id, job in list(self._jobs.items()):
            if job.get("run_id") == run_id:
                job["stop_event"].set()
                stopped = True
                self.workspace.log_activity(
                    f"Stop requested for run {run_id[:8]} (job #{job_id})",
                    "WARN",
                    run_id=run_id,
                )
        return stopped

    def is_run_active(self, run_id: str) -> bool:
        """Check if a run is currently executing in background."""
        return any(job.get("run_id") == run_id for job in self._jobs.values())

    # ------------------------------------------------------------------ routing messages to workspace

    def on_job_started(self, event: JobStarted) -> None:
        self.workspace.on_job_started(event)
        if getattr(self, "unsubscribe_screen", None) and self.unsubscribe_screen.is_mounted:
            self.unsubscribe_screen.on_job_started(event)
        if getattr(self, "runs", None) and self.runs.is_mounted:
            self.runs.refresh_rows()

    def on_job_progress(self, event: JobProgress) -> None:
        if isinstance(event.payload, dict) and "run_id" in event.payload:
            job = self._jobs.get(event.job_id)
            if job and not job.get("run_id"):
                job["run_id"] = event.payload["run_id"]
                if getattr(self, "runs", None) and self.runs.is_mounted:
                    self.runs.refresh_rows()
        self.workspace.job_progress(event)

    def on_job_finished(self, event: JobFinished) -> None:
        self._jobs.pop(event.job_id, None)
        self.workspace.job_finished(event)
        if getattr(self, "unsubscribe_screen", None) and self.unsubscribe_screen.is_mounted:
            self.unsubscribe_screen.on_job_finished(event)
        if getattr(self, "runs", None) and self.runs.is_mounted:
            self.runs.refresh_rows()

    def on_unmount(self):
        self.controller.activate_run(None)
        self.controller.repository.lock()


def launch(config: Path, data_dir: Path):
    controller = TUIController(load_settings(config), data_dir, config_path=config)
    InboxedApp(controller, config).run()
