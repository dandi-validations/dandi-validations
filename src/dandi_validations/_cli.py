"""Provide the ``dandi-validations`` command-line interface."""

import argparse
import collections.abc
import importlib.metadata
import json
import logging
import os
import pathlib
import sys

from ._capsule import prepare_capsule
from ._collect import collect
from ._git import repository_root
from ._globals import DEFAULT_PARAMS_KEY, ORGANIZATION
from ._repositories import (
    DEFAULT_REMOTE_URL_TEMPLATE,
    GitHub,
    dispatch_validations,
    ensure_repositories,
    list_dandisets,
)
from ._run import run_job
from ._version_code import VersionCodeError, build_version_code, versions_from_pixi_lock

_FINE_GRAINED_PREFIX = "github_pat_"


def _token() -> str:
    token = os.environ.get("_GITHUB_API_KEY")
    if token:
        if not token.startswith(_FINE_GRAINED_PREFIX):
            message = (
                "`_GITHUB_API_KEY` must be a fine-grained personal access token, which starts with "
                f"`{_FINE_GRAINED_PREFIX}`. Classic tokens are not supported."
            )
            raise SystemExit(message)
        return token
    # The built-in token of GitHub Actions, enough to read the organization's public repositories.
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        message = "Set `_GITHUB_API_KEY` to a fine-grained personal access token that can administer the organization."
        raise SystemExit(message)
    return token


def _dandiset_ids(*, arguments: argparse.Namespace, from_organization: bool = False) -> list[str]:
    if arguments.dandisets:
        identifiers = sorted(set(arguments.dandisets))
    elif from_organization:
        identifiers = GitHub(token=_token(), organization=arguments.organization).list_dandiset_repositories()
    else:
        identifiers = list_dandisets(arguments.source)
    if arguments.limit is not None:
        identifiers = identifiers[: arguments.limit]
    return identifiers


def _add_selection_arguments(*, parser: argparse.ArgumentParser, source: bool) -> None:
    parser.add_argument("--dandisets", nargs="*", default=None, help="Only these Dandisets.")
    parser.add_argument("--limit", type=int, default=None, help="At most this many Dandisets.")
    parser.add_argument("--organization", default=ORGANIZATION)
    if source:
        parser.add_argument(
            "--source",
            choices=["api", "s3"],
            default="api",
            help="Where to list Dandisets from (the API lists only non-empty, open ones).",
        )


def _add_capsule_arguments(parser: argparse.ArgumentParser, /) -> None:
    parser.add_argument("--dandiset", required=True, help="The six-digit identifier of the Dandiset.")
    parser.add_argument("--version", default="draft", help="The Dandiset version (draft, or a published one).")
    parser.add_argument("--params", default=DEFAULT_PARAMS_KEY, help="The registered parameter set.")
    parser.add_argument("--force", action="store_true", help="Validate even if the capsule validated these inputs.")
    parser.add_argument("--path", type=pathlib.Path, default=pathlib.Path.cwd(), help="A path in the clone of main.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dandi-validations", description=__doc__)
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {importlib.metadata.version('dandi-validations')}",
    )
    parser.add_argument("--verbosity", default="INFO", help="The logging level.")
    commands = parser.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser("run", help="Form, execute, record and push a validation job capsule.")
    _add_capsule_arguments(run_parser)
    run_parser.add_argument("--push", metavar="REMOTE", default=None, help="Push the capsule to this remote.")
    run_parser.add_argument("--branch", default=None, help="The branch to push main to (default: main).")
    run_parser.add_argument("--timeout", type=float, default=0, help="Stop the run after this many minutes.")
    run_parser.add_argument(
        "--manifest",
        type=pathlib.Path,
        default=None,
        help="A directory to read assets.jsonld and dandiset.yaml from, instead of the archive's S3 bucket.",
    )
    run_parser.add_argument("--claim", action="store_true", help="Only form and claim the capsule. Do not run it.")

    prepare_parser = commands.add_parser("prepare", help="Only form the job capsule, without git.")
    _add_capsule_arguments(prepare_parser)

    list_parser = commands.add_parser("list-dandisets", help="List the Dandisets to validate.")
    _add_selection_arguments(parser=list_parser, source=True)

    code_parser = commands.add_parser(
        "version-code",
        help="Print the validator version code (dandi/dandi-archive#2987) of a lock or an image.",
    )
    code_source = code_parser.add_mutually_exclusive_group(required=True)
    code_source.add_argument("--lock", type=pathlib.Path, help="A pixi.lock to read the versions from.")
    code_source.add_argument(
        "--versions",
        type=pathlib.Path,
        help="The output of the image's dandi-validation-versions ('-' for stdin).",
    )

    ensure_parser = commands.add_parser(
        "ensure-repos",
        help="Create missing Dandiset repositories and sync the template into them.",
    )
    _add_selection_arguments(parser=ensure_parser, source=True)
    ensure_parser.add_argument("--ref", default="main", help="The ref of this repository that the template uses.")
    ensure_parser.add_argument("--existing", action="store_true", help="Only sync repositories that exist.")
    ensure_parser.add_argument(
        "--url",
        default=None,
        help="The remote URL, with {token}, {organization} and {repository} fields (default: GitHub over HTTPS).",
    )

    dispatch_parser = commands.add_parser("dispatch", help="Start the Validate workflow of Dandiset repositories.")
    _add_selection_arguments(parser=dispatch_parser, source=False)
    dispatch_parser.add_argument("--force", action="store_true")
    dispatch_parser.add_argument("--version", default="draft", help="The Dandiset version to validate.")

    collect_parser = commands.add_parser("collect", help="Collect the capsules of every Dandiset repository.")
    _add_selection_arguments(parser=collect_parser, source=False)
    collect_parser.add_argument("--output", type=pathlib.Path, default=pathlib.Path("records"))
    collect_parser.add_argument(
        "--tags",
        action="store_true",
        help="Write one table per capsule tag, each in a directory named by the tag.",
    )
    return parser


def _run(arguments: argparse.Namespace, /) -> int:
    return run_job(
        path=arguments.path,
        dandiset_id=arguments.dandiset,
        dandiset_version=arguments.version,
        params_key=arguments.params,
        force=arguments.force,
        push_to=arguments.push,
        branch=arguments.branch,
        time_limit_minutes=arguments.timeout,
        manifest_directory=arguments.manifest,
        execute=not arguments.claim,
    )


def _prepare(arguments: argparse.Namespace, /) -> int:
    result = prepare_capsule(
        repository_root=repository_root(arguments.path),
        dandiset_id=arguments.dandiset,
        dandiset_version=arguments.version,
        params_key=arguments.params,
        force=arguments.force,
    )
    print(result.reason)
    if result.capsule is not None:
        print(result.capsule.path)
    return 0


def _list_dandisets(arguments: argparse.Namespace, /) -> int:
    print("\n".join(_dandiset_ids(arguments=arguments)))
    return 0


def _version_code(arguments: argparse.Namespace, /) -> int:
    if arguments.lock is not None:
        versions = versions_from_pixi_lock(arguments.lock.read_text())
    else:
        text = sys.stdin.read() if str(arguments.versions) == "-" else arguments.versions.read_text()
        versions = json.loads(text)["validator_versions"]
    try:
        print(build_version_code(versions))
    except VersionCodeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


def _ensure_repositories(arguments: argparse.Namespace, /) -> int:
    outcomes = ensure_repositories(
        dandiset_ids=_dandiset_ids(arguments=arguments),
        token=_token(),
        codebase_ref=arguments.ref,
        organization=arguments.organization,
        remote_url_template=arguments.url or DEFAULT_REMOTE_URL_TEMPLATE,
        create=not arguments.existing,
    )
    return 1 if any(outcome.startswith("error") for outcome in outcomes.values()) else 0


def _dispatch(arguments: argparse.Namespace, /) -> int:
    outcomes = dispatch_validations(
        dandiset_ids=_dandiset_ids(arguments=arguments, from_organization=True),
        token=_token(),
        organization=arguments.organization,
        force=arguments.force,
        dandiset_version=arguments.version,
    )
    return 1 if any(outcome.startswith("error") for outcome in outcomes.values()) else 0


def _collect(arguments: argparse.Namespace, /) -> int:
    written = collect(
        dandiset_ids=_dandiset_ids(arguments=arguments, from_organization=True),
        output_directory=arguments.output,
        organization=arguments.organization,
        by_tag=arguments.tags,
    )
    print("\n".join(str(path) for path in written))
    return 0


_COMMANDS: dict[str, collections.abc.Callable[[argparse.Namespace], int]] = {
    "run": _run,
    "prepare": _prepare,
    "list-dandisets": _list_dandisets,
    "version-code": _version_code,
    "ensure-repos": _ensure_repositories,
    "dispatch": _dispatch,
    "collect": _collect,
}


def main(argv: list[str] | None = None, /) -> int:
    """
    Run the ``dandi-validations`` command line.

    Parameters
    ----------
    argv : list[str] or None, default=None
        The arguments, if not those of this process.

    Returns
    -------
    int
        The exit code.

    """
    arguments = _build_parser().parse_args(argv)
    logging.basicConfig(level=arguments.verbosity.upper(), format="%(levelname)s %(name)s: %(message)s")
    return _COMMANDS[arguments.command](arguments)


if __name__ == "__main__":
    sys.exit(main())
