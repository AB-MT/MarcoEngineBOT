import pytest


def test_nothing():
    assert True

def download_me():
    response = requests.get("https://github.com/mizar/YaneuraOu/releases/download/v7.0.0/Suisho5-YaneuraOu-v7.0.0-windows.zip", allow_redirects=True)
    with open("yo.zip", "wb") as file:
        file.write(response.content)
    with zipfile.ZipFile("yo.zip", "r") as zip_ref:
        zip_ref.extractall(".")
    shutil.copyfile(f"YaneuraOu_NNUE-tournament-clang++-sse42.exe", f"yo.exe")
