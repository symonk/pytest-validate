# -*- coding: utf-8 -*-
from __future__ import annotations

import functools
import logging
from concurrent.futures._base import Future
from dataclasses import dataclass
from typing import List
from typing import Optional
from typing import Set

import pytest
from _pytest.config import Config
from _pytest.config import PytestPluginManager
from _pytest.terminal import TerminalReporter
from infrastructure import InfrastructureFunction
from infrastructure import InfrastructureFunctionManager
from infrastructure.utility.constants import INFRASTRUCTURE_PLUGIN_NAME
from infrastructure.utility.import_utilities import import_module_from_path
from infrastructure.utility.plugin_utilities import can_plugin_be_registered

logger = logging.getLogger(__name__)


def pytest_addoption(parser):
    group = parser.getgroup(INFRASTRUCTURE_PLUGIN_NAME)
    group.addoption(
        "--skip-infra",
        action="store_true",
        default=False,
        dest="skip_infra",
        help="Bypass the validation functions and execute testing without checking, disable the plugin completely",
    )
    group.addoption(
        "--infra-module",
        action="store",
        dest="infra_module",
        help="Module containing @infrastructure decorated functions",
    )
    group.addoption(
        "--max-workers",
        action="store",
        type=int,
        default=2,
        dest="max_workers",
        help="How many max workers should the thread (or process) pool executor be permitted to use.",
    )
    group.addoption(
        "--infra-env",
        action="store",
        dest="infra_env",
        help="Runtime environment; only_on_env= of validation functions will account for this"
        "Note: if not specified, all infrastructure functions will be executed.",
    )
    group.addoption(
        "--use-processes",
        action="store_true",
        default=False,
        dest="use_processes",
        help="Execute via a process pool, rather than a thread pool."
        "Depending on IO bound etc, your infra functions may be better "
        "distributed via processes, not threads.",
    )
    group.addoption(
        "--soft-validate",
        action="store_true",
        default=False,
        dest="soft_validate",
        help="When enabled, validation failure(s) will not abort pytest early, but continue.  Terminal summary will"
        "clearly indicate infrastructure related issues afterwards.",
    )


@pytest.hookimpl
def pytest_addhooks(pluginmanager: PytestPluginManager) -> None:
    from infrastructure.hookspecs import InfrastructureHookSpecs

    pluginmanager.add_hookspecs(InfrastructureHookSpecs)


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    if can_plugin_be_registered(config):
        infra_plugin = PytestValidate(config)
        config.pluginmanager.register(infra_plugin, INFRASTRUCTURE_PLUGIN_NAME)
        collected = config.pluginmanager.hook.pytest_infrastructure_perform_collect(
            module_path=config.getoption("infra_module")
        )
        config.pluginmanager.hook.pytest_infrastructure_collect_modifyitems(
            items=collected[-1]
        )
        infra_plugin.validate_infrastructure(config)


class PytestValidate:
    """
    The pytest infrastructure plugin object;
    This plugin is only registered if the --bypass-infrastructure arg is not provided, else it is completely skipped!
    """

    def __init__(self, config):
        self.config = config
        self.functions = None
        self.environment = config.getoption("infra_env")
        self.thread_count = config.getoption("max_workers")
        self.infra_module = config.getoption("infra_module")
        self.infra_manager: InfrastructureFunctionManager = InfrastructureFunctionManager()

    @pytest.hookimpl(tryfirst=True)
    def pytest_infrastructure_perform_collect(
        self, module_path: str
    ) -> List[InfrastructureFunction]:
        infra_mod = import_module_from_path(module_path)
        from inspect import isfunction, getmembers

        infra_functions = [
            func[1].infra_function
            for func in getmembers(infra_mod)
            if isfunction(func[1]) and hasattr(func[1], "infra_function")
        ]
        return infra_functions

    @pytest.hookimpl(tryfirst=True)
    def pytest_infrastructure_collect_modifyitems(
        self, items: List[Optional[InfrastructureFunction]]
    ) -> None:
        """
        Default behaviour is to use the infrastructure functions which have been imported via --infra-module
        """
        for func in items:
            self.infra_manager.register(func)
        items[:] = self.infra_manager.get_applicable(self.environment)

    def validate_infrastructure(self, config: Config) -> None:
        from concurrent.futures import ThreadPoolExecutor
        from concurrent.futures import ProcessPoolExecutor
        from concurrent.futures import as_completed

        bound_count = config.getoption("max_workers")
        executor_instance = (
            ThreadPoolExecutor
            if not config.getoption("use_processes")
            else ProcessPoolExecutor
        )

        parallel, isolated = self.infra_manager.get_applicable(self.environment)
        results = []
        with executor_instance(max_workers=bound_count) as executor:
            futures: List[Future] = []
            for non_isolated_function in parallel:
                futures.append(executor.submit(non_isolated_function))
            for future in as_completed(futures):
                results.append(future.result())

    @pytest.fixture
    def infra_functions(self) -> List[InfrastructureFunction]:
        return self.infra_manager.get_squashed(self.environment)

    @pytest.hookimpl()
    def pytest_terminal_summary(self, terminalreporter: TerminalReporter) -> None:
        functions = self.infra_manager.get_squashed(self.environment)
        terminalreporter.write_sep("-", "pytest-infrastructure results")
        terminalreporter.write_line(f"{repr(self._resolve_meta_data())}")
        if not functions:
            terminalreporter.write_line(
                "no pytest-infrastructure functions collected & executed."
            )
        else:
            for function in functions:
                terminalreporter.write_line(repr(function))

    def _resolve_meta_data(self) -> ExecutionMetaData:
        return ExecutionMetaData(
            infra_module=self.config.getoption("infra_module"),
            soft_run=self.config.getoption("soft_validate"),
            infra_environment=self.config.getoption("infra_env"),
            max_workers=self.config.getoption("max_workers"),
            use_processes=self.config.getoption("use_processes"),
        )


@dataclass(frozen=True, repr=True)
class ExecutionMetaData:
    """
    Dataclass for encapsulation of the infrastructure execution meta data.
    :param infra_module: The string (full path) to a module that contains @infrastructure decorated functions
    :param soft_run: Are validation failure(s) enough to terminate the run, or should pytest continue executing tests.
    :param infra_environment: The infra environment, used to exclude/ignore certain functions.
    :param max_workers: Executor max_workers for the run.
    :param use_processes: Use a process pool executor rather than the thread pool executor by default.
    """

    infra_module: str
    soft_run: bool
    infra_environment: str
    max_workers: int
    use_processes: str


def infrastructure(
    ignored_on: Optional[Set[str]] = None, order: int = 0, name: str = None
):
    """
    Bread and button of pytest-infrastructure.  Stores implementations of the decorator globally
    which are then available to the PytestValidate plugin to invoke and apply its custom logic to the pytest run.
    """

    def decorator(func):
        infra_function = InfrastructureFunction(
            executable=func, ignored_on=ignored_on, order=order, name=name
        )
        func.infra_function = infra_function

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            return result

        return wrapper

    return decorator
