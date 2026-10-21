"""Source Adapter contract: plan / parse / build / plan_recheck over archived inputs.

Adapters never perform I/O or write the database. They describe requests and document
targets, read already archived inputs through the Runtime context and return candidates.
"""

from importlib import import_module

from wlh.adapters.records import RawRecord, RawUnitSource, RequestSpec
from wlh.adapters.session import CasSessionProxy
from wlh.core import WlhError

MODULES = {
    "structured_xml": "wlh.adapters.structured_xml",
    **{
        name: "wlh.adapters.bridges." + name
        for name in (
            "worldwidelaw_bridge",
            "juriscraper_bridge",
            "oldp_bridge",
            "openstates_bridge",
            "opencaselaw_bridge",
        )
    },
    "web": "wlh.adapters.web",
    "federal_register": "wlh.adapters.federal_register",
}


def load(name):
    if name not in MODULES:
        raise WlhError("unknown_adapter")
    try:
        return import_module(MODULES[name]).Adapter
    except ModuleNotFoundError:
        raise WlhError("adapter_not_implemented") from None


def contract(name):
    return load(name).CONTRACT


def semantic_options(name, options):
    """Only parsing semantics enter the parser fingerprint; pagination selects scope."""
    planning = load(name).PLANNING_OPTIONS
    return {key: value for key, value in options.items() if key not in planning}


def validate_options(name, options):
    adapter = load(name)
    try:
        adapter.validate_options(options)
    except (ValueError, TypeError, KeyError):
        raise WlhError("invalid_adapter_options") from None


class Base:
    """Shared defaults; subclasses define CONTRACT and the parsing rules."""

    CONTRACT = "wlh.adapter.base.v1"
    PLANNING_OPTIONS = ()

    def __init__(self, config):
        self.config = config

    @staticmethod
    def validate_options(options):
        if options:
            raise ValueError("unexpected_adapter_options")

    def plan(self, state):
        """Initial requests and targets for a collect Run; ``state`` is adapter_state."""
        return {
            "requests": [
                {"url": url, "role": "page", "depth": 0} for url in self.config["entry_urls"]
            ],
            "targets": [],
            "state": state,
        }

    def plan_recheck(self, resources, state):
        """Due Resources (stable identity, public_url, recheck_locator, current Version)."""
        targets = []
        for resource in resources:
            locator = resource["recheck_locator"]
            if not locator.get("url"):
                raise WlhError("recheck_locator_unsupported")
            request = {"url": locator["url"], "role": locator.get("role", "page"), "depth": 0}
            # The known identity is registered up front, so a failed recheck is still
            # recorded as a failed delivery for that Resource.
            targets.append(
                {
                    "identity": {
                        "namespace": resource["namespace"],
                        "external_key": resource["external_key"],
                        "public_url": resource["public_url"],
                        "recheck_locator": locator,
                    },
                    "inputs": [{"request": request, "role": "detail"}],
                }
            )
        return {"requests": [], "targets": targets, "state": state}

    def parse(self, ctx, task, acquisition):
        raise NotImplementedError

    def build(self, ctx, target, inputs):
        raise NotImplementedError


__all__ = ["RawRecord", "RawUnitSource", "RequestSpec", "CasSessionProxy", "Base", "load"]
