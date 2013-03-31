check:
	pep8 autoflake autoflake.py setup.py
	pep257 autoflake autoflake.py setup.py
	pylint --report=no --include-ids=yes --disable=C0103,R0914,W0142,W0404,W0511,W0622 --rcfile=/dev/null autoflake.py setup.py
	python setup.py --long-description | rst2html --strict > /dev/null
	scspell autoflake autoflake.py setup.py test_autoflake.py README.rst

coverage:
	@rm -f .coverage
	@coverage run test_autoflake.py
	@coverage report
	@coverage html
	@rm -f .coverage
	@python -m webbrowser -n "file://${PWD}/htmlcov/index.html"

mutant:
	@mut.py --disable-operator RIL -t autoflake -u test_autoflake -mc

readme:
	@restview --long-description

register:
	# TODO: HTTPS does not work. Try using distribute (in try/catch).
	@python setup.py register sdist upload --repository=https://pypi.python.org/pypi
	@srm ~/.pypirc
