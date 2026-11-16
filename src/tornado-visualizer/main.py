"""Tornado Visualizer - programma indipendente.   python main.py [file ...]"""
import sys

if __name__ == "__main__":
    from tornado_suite.standalone import main
    sys.exit(main("visualizer"))
