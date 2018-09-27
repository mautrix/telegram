import setuptools
import glob
import mautrix_telegram

extras = {
    "highlight_edits": ["lxml>=4.1.1,<5"],
    "better_formatter": ["lxml>=4.1.1,<5"],
    "fast_crypto": ["cryptg>=0.1,<0.2"],
    "webp_convert": ["Pillow>=5.0.0,<6"],
    "hq_thumbnails": ["moviepy>=0.2,<0.3"],
}
extras["all"] = list(set(deps[0] for deps in extras.values()))

setuptools.setup(
    name="mautrix-telegram",
    version=mautrix_telegram.__version__,
    url="https://github.com/tulir/mautrix-telegram",

    author="Tulir Asokan",
    author_email="tulir@maunium.net",

    description="A Matrix-Telegram hybrid puppeting/relaybot bridge.",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",

    packages=setuptools.find_packages(),

    install_requires=[
        "aiohttp>=3.0.1,<4",
        "mautrix-appservice>=0.3.7,<0.4.0",
        "SQLAlchemy>=1.2.3,<2",
        "alembic>=1.0.0,<2",
        "commonmark>=0.8.1,<1",
        "ruamel.yaml>=0.15.35,<0.16",
        "future-fstrings>=0.4.2",
        "python-magic>=0.4.15,<0.5",
        "telethon>=1.0,<1.3",
        "telethon-session-sqlalchemy>=0.2.3,<0.3",
    ],
    extras_require=extras,

    classifiers=[
        "Development Status :: 4 - Beta",
        "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)",
        "Topic :: Communications :: Chat",
        "Framework :: AsyncIO",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
    ],
    entry_points="""
        [console_scripts]
        mautrix-telegram=mautrix_telegram.__main__:main
    """,
    package_data={"mautrix_telegram": [
        "web/public/*.mako", "web/public/*.png", "web/public/*.css",
    ]},
    data_files=[
        (".", ["example-config.yaml", "alembic.ini"]),
        ("alembic", ["alembic/env.py"]),
        ("alembic/versions", glob.glob("alembic/versions/*.py"))
    ],
)

