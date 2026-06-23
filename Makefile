.PHONY: docker lint

docker:
	docker compose down
	docker compose up -d --build
	docker compose ps -a

lint:
	tox -e all
	make commit
	

fmt:
	tox -e format

commit:
	git add .
	git commit -m "lint: run tox and fix issues" || echo "nothing to commit"