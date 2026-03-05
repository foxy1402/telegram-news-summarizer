from pyrogram import Client


def main() -> None:
    print("Generate TELEGRAM_USER_SESSION_STRING")
    api_id = int(input("TELEGRAM_API_ID: ").strip())
    api_hash = input("TELEGRAM_API_HASH: ").strip()

    # Uses an ephemeral local session name only for login/export.
    with Client("session_maker", api_id=api_id, api_hash=api_hash) as app:
        session_string = app.export_session_string()

    print("\nCopy this into your .env:")
    print(f"TELEGRAM_USER_SESSION_STRING={session_string}")


if __name__ == "__main__":
    main()
