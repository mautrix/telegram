import setuptools
import mautrix_telegram

setuptools.setup(
    name="mautrix-telegram",
    version=mautrix_telegram.__version__,
    url="https://github.com/tulir/mautrix-telegram",

    author="Tulir Asokan",
    author_email="tulir@maunium.net",

    description="A Matrix-Telegram puppeting bridge.",
    long_description=open("README.md").read(),

    packages=setuptools.find_packages(),

    install_requires=[
        "aiohttp>=2.3.10,<3",
        "SQLAlchemy>=1.2.2,<2",
        "alembic>=0.9.7",
        "Markdown>=2.6.11,<3",
        "ruamel.yaml>=0.15.35,<0.16",
        "Pillow>=5.0.0,<6",
        "future-fstrings>=0.4.1",
        "python-magic>=0.4.15,<0.5",
    ],
    dependency_links=[
        "https://github.com/LonamiWebs/Telethon/tarball/7da092894b306d720cc60c04daa2bfba58f81946#egg=Telethon"
    ],

    classifiers=[
        "Development Status :: 4 Beta",
        "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
        "Topic :: Communications :: Chat",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
    ],
    entry_points="""
        [console_scripts]
        mautrix-telegram=mautrix_telegram.__main__:main
    """,
)
