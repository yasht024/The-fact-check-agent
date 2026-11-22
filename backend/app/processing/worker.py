from backend.app.config import Settings


def main() -> None:
    try:
        Settings.from_env()
    except ValueError as error:
        raise SystemExit(str(error)) from None

    print("Worker configuration loaded. Job processing will be added in a later phase.")


if __name__ == "__main__":
    main()
