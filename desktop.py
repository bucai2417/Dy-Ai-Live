from multiprocessing import freeze_support

from app.launch import main


if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
