"""Entrypoint for the sample repository."""

from sample_pkg.service import Directory, build_user


def main() -> None:
    """Build one user and add them to a directory."""

    user = build_user("Ada Lovelace")
    directory = Directory()
    directory.add(user)


if __name__ == "__main__":
    main()
