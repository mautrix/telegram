import setuptools

setuptools.setup(
    name="mautrix_telegram",
    version="0.1.0",
    url="https://github.com/tulir/mautrix-telegram",

    author="Tulir Asokan",
    author_email="tulir@maunium.net",

    description="A Matrix-Telegram puppeting bridge.",
    long_description=open("README.md").read(),

    packages=setuptools.find_packages(),

    install_requires=["telethon", "matrix-client", "sqlalchemy"],

    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
    ],
    entry_points="""
        [console_scripts]
        mautrix-telegram=mautrix_telegram.__main__:main
    """,
)
