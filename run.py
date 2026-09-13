from dotenv import load_dotenv
load_dotenv()  # Reads .env into os.environ — must happen before app import

from app import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
