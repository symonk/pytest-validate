# how to release (test-pypi for now)
python setup.py sdist bdist_wheel
twine upload
