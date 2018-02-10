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
        "Telethon>=0.17.0.0,<0.18",
        "aiohttp>=2.3.10,<3",
        "SQLAlchemy>=1.2.2,<2",
        "Markdown>=2.6.11,<3",
        "ruamel.yaml>=0.15.35,<0.16",
        "Pillow>=5.0.0,<6",
        "future-fstrings>=0.4.1",
        "python-magic>=0.4.15,<0.5",
    ],
    dependency_links=[
        "https://github.com/Cadair/matrix-python-sdk/tarball/1fab9821d98d15769e44e66f714d00a32a48d692#egg=matrix_client"
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
