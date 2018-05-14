import setuptools
import sys
import glob
import mautrix_telegram

extras = {
    "highlight_edits": ["lxml>=4.1.1,<5"],
    "fast_crypto": ["cryptg>=0.1,<0.2"],
    "webp_convert": ["Pillow>=5.0.0,<6"],
    "hq_thumbnails": ["moviepy>=0.2,<0.3"],
}
extras["all"] = [deps[0] for deps in extras.values()]

setuptools.setup(
    name="mautrix-telegram",
    version=mautrix_telegram.__version__,
    url="https://github.com/tulir/mautrix-telegram",

    author="Tulir Asokan",
    author_email="tulir@maunium.net",

    description="A Matrix-Telegram hybrid puppeting/relaybot bridge.",
    long_description=open("README.md").read(),

    packages=setuptools.find_packages(),

    install_requires=[
        "aiohttp>=3.0.1,<4",
        "mautrix-appservice>=0.1.4,<0.2.0",
        "SQLAlchemy>=1.2.3,<2",
        "alembic>=0.9.8,<0.10",
        "Markdown>=2.6.11,<3",
        "ruamel.yaml>=0.15.35,<0.16",
        "future-fstrings>=0.4.2",
        "python-magic>=0.4.15,<0.5",
        "telethon-aio>=0.19.0,<0.19.1",
        "telethon-session-sqlalchemy>=0.2.3,<0.3",
    ],
    extras_require=extras,

    classifiers=[
        "Development Status :: 4 - Beta",
        "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)",
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
    package_data={"mautrix_telegram": [
        "public/*.mako", "public/*.png", "public/*.css",
    ]},
    data_files=[
        (".", ["example-config.yaml", "alembic.ini"]),
        ("alembic", ["alembic/env.py"]),
        ("alembic/versions", glob.glob("alembic/versions/*.py"))
    ],
)

