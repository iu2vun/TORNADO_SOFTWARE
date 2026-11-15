"""Tornado Shell - programma indipendente.   python main.py [file ...] [--admin|--no-admin]"""
import sys

if __name__ == "__main__":
    from tornado_suite.standalone import main
    sys.exit(main("shell"))
