.PHONY: docker lint pi-setup

docker:
	docker compose down
	docker compose up -d --build
	docker compose ps -a

lint:
	tox -e all
	make commit
	

fmt:
	tox -e format
	make commit
	git push

pi-setup:
	sudo bash scripts/setup-pi.sh

commit:
	git add .
	git commit -m "lint: run tox and fix issues" || echo "nothing to commit"