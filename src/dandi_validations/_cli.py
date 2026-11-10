"""Command line interface: ``dandi-validations {run,prepare,list-dandisets,ensure-repos,dispatch,collect}``."""

import argparse
import logging
import os
import pathlib
import sys

from ._globals import DEFAULT_PARAMS_KEY, ORGANIZATION


def _token() -> str:
    token = os.environ.get("_GITHUB_API_KEY") or os.environ.get("GITHUB_TOKEN")
    if not token:
        message = "Set `_GITHUB_API_KEY` (or `GITHUB_TOKEN`) to a token that can administer the organization."
        raise SystemExit(message)
    return token


def _dandiset_ids(arguments: argparse.Namespace, *, from_organization: bool = False) -> list[str]:
    if arguments.dandiset_ids:
        identifiers = sorted(set(arguments.dandiset_ids))
    elif from_organization:
        from ._repositories import GitHub

        identifiers = GitHub(_token(), organization=arguments.organization).list_dandiset_repositories()
    else:
        from ._repositories import list_dandisets

        identifiers = list_dandisets(source=arguments.source)
    if arguments.limit is not None:
        identifiers = identifiers[: arguments.limit]
    return identifiers


def _add_selection_arguments(parser: argparse.ArgumentParser, *, source: bool) -> None:
    parser.add_argument("--dandiset-ids", nargs="*", default=None, help="Only these Dandisets.")
    parser.add_argument("--limit", type=int, default=None, help="At most this many Dandisets.")
    parser.add_argument("--organization", default=ORGANIZATION)
    if source:
        parser.add_argument(
            "--source",
            choices=["api", "s3"],
            default="api",
            help="Where to list Dandisets from (the API lists only non-empty, open ones).",
        )


def main(argv: list[str] | None = None) -> int:
    from . import __version__

    parser = argparse.ArgumentParser(prog="dandi-validations", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--log-level", default="INFO")
    commands = parser.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser("run", help="Form, execute, record (and push) a validation job capsule.")
    run_parser.add_argument("--dandiset-id", required=True)
    run_parser.add_argument("--dandiset-version", default="draft")
    run_parser.add_argument("--params", default=DEFAULT_PARAMS_KEY, help="Registered parameter set.")
    run_parser.add_argument("--force", action="store_true", help="Validate even if an equivalent job succeeded.")
    run_parser.add_argument("--push", metavar="REMOTE", default=None, help="Push the capsule to this remote.")
    run_parser.add_argument("--branch", default=None, help="Branch to push to (default: main).")
    run_parser.add_argument("--time-limit-minutes", type=float, default=0, help="Stop the run after this long.")
    run_parser.add_argument("--path", type=pathlib.Path, default=pathlib.Path.cwd())
    run_parser.add_argument(
        "--manifest-directory",
        type=pathlib.Path,
        default=None,
        help="Read assets.jsonld and dandiset.yaml from here instead of the archive's S3 bucket.",
    )
    run_parser.add_argument("--claim-only", action="store_true", help="Form and claim the capsule, but do not run it.")

    prepare_parser = commands.add_parser("prepare", help="Only form the job capsule (do not run it).")
    prepare_parser.add_argument("--dandiset-id", required=True)
    prepare_parser.add_argument("--dandiset-version", default="draft")
    prepare_parser.add_argument("--params", default=DEFAULT_PARAMS_KEY)
    prepare_parser.add_argument("--force", action="store_true")
    prepare_parser.add_argument("--path", type=pathlib.Path, default=pathlib.Path.cwd())

    list_parser = commands.add_parser("list-dandisets", help="List the Dandisets to validate.")
    _add_selection_arguments(list_parser, source=True)

    code_parser = commands.add_parser(
        "version-code", help="Print the validator version code (dandi/dandi-archive#2987) of a lock or an image."
    )
    code_source = code_parser.add_mutually_exclusive_group(required=True)
    code_source.add_argument("--pixi-lock", type=pathlib.Path, help="A pixi.lock to read the versions from.")
    code_source.add_argument(
        "--versions-json",
        type=pathlib.Path,
        help="The output of the image's dandi-validation-versions ('-' for stdin).",
    )

    ensure_parser = commands.add_parser(
        "ensure-repos", help="Create missing Dandiset repositories and sync the template into them."
    )
    _add_selection_arguments(ensure_parser, source=True)
    ensure_parser.add_argument("--codebase-ref", default="main", help="Ref of this repository the template uses.")
    ensure_parser.add_argument("--no-create", action="store_true", help="Only sync repositories that exist.")
    ensure_parser.add_argument(
        "--remote-url-template",
        default=None,
        help="Remote URL with {token}, {organization} and {repository} fields (default: GitHub over HTTPS).",
    )

    dispatch_parser = commands.add_parser("dispatch", help="Trigger the validation workflow of Dandiset repositories.")
    _add_selection_arguments(dispatch_parser, source=False)
    dispatch_parser.add_argument("--force", action="store_true")
    dispatch_parser.add_argument("--dandiset-version", default="draft")

    collect_parser = commands.add_parser("collect", help="Collect every Dandiset repository's jobs table.")
    _add_selection_arguments(collect_parser, source=False)
    collect_parser.add_argument("--output", type=pathlib.Path, default=pathlib.Path("records"))

    arguments = parser.parse_args(argv)
    logging.basicConfig(level=arguments.log_level.upper(), format="%(levelname)s %(name)s: %(message)s")

    if arguments.command == "run":
        from ._run import run_job

        return run_job(
            path=arguments.path,
            dandiset_id=arguments.dandiset_id,
            dandiset_version=arguments.dandiset_version,
            params_key=arguments.params,
            force=arguments.force,
            push_to=arguments.push,
            branch=arguments.branch,
            time_limit_minutes=arguments.time_limit_minutes,
            manifest_directory=arguments.manifest_directory,
            execute=not arguments.claim_only,
        )

    if arguments.command == "prepare":
        from ._capsule import prepare_capsule
        from ._git import repository_root

        result = prepare_capsule(
            repository_root=repository_root(arguments.path),
            dandiset_id=arguments.dandiset_id,
            dandiset_version=arguments.dandiset_version,
            params_key=arguments.params,
            force=arguments.force,
        )
        print(result.reason)
        if result.capsule is not None:
            print(result.capsule.path)
        return 0

    if arguments.command == "list-dandisets":
        print("\n".join(_dandiset_ids(arguments)))
        return 0

    if arguments.command == "version-code":
        import json

        from ._version_code import VersionCodeError, build_version_code, versions_from_pixi_lock

        if arguments.pixi_lock is not None:
            versions = versions_from_pixi_lock(arguments.pixi_lock.read_text())
        else:
            text = sys.stdin.read() if str(arguments.versions_json) == "-" else arguments.versions_json.read_text()
            versions = json.loads(text)["validator_versions"]
        try:
            print(build_version_code(versions))
        except VersionCodeError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        return 0

    if arguments.command == "ensure-repos":
        from ._repositories import DEFAULT_REMOTE_URL_TEMPLATE, ensure_repositories

        outcomes = ensure_repositories(
            dandiset_ids=_dandiset_ids(arguments),
            token=_token(),
            codebase_ref=arguments.codebase_ref,
            organization=arguments.organization,
            remote_url_template=arguments.remote_url_template or DEFAULT_REMOTE_URL_TEMPLATE,
            create=not arguments.no_create,
        )
        return 1 if any(outcome.startswith("error") for outcome in outcomes.values()) else 0

    if arguments.command == "dispatch":
        from ._repositories import dispatch_validations

        outcomes = dispatch_validations(
            dandiset_ids=_dandiset_ids(arguments, from_organization=True),
            token=_token(),
            organization=arguments.organization,
            force=arguments.force,
            dandiset_version=arguments.dandiset_version,
        )
        return 1 if any(outcome.startswith("error") for outcome in outcomes.values()) else 0

    if arguments.command == "collect":
        from ._collect import collect

        written = collect(
            dandiset_ids=_dandiset_ids(arguments, from_organization=True),
            output_directory=arguments.output,
            organization=arguments.organization,
        )
        print("\n".join(str(path) for path in written))
        return 0

    parser.error(f"Unknown command {arguments.command!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
