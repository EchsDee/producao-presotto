# Atualizador WSGI da KingHost

Versao preparada: 1.0.4-compat.4. Requirements preservado, sem pip install.
Nao modifica o conteudo de app.wsgi, o backup diario, a venv ou a localizacao do banco.
O reinicio e solicitado apenas atualizando a data do app.wsgi existente.

## Primeira instalacao

A versao 1.0.1 nao executa instalacoes WSGI. E necessario habilitar o novo updater uma unica vez por SSH.
Envie presotto-update.zip e enable_wsgi_updates.py desta mesma entrega para /home/pressotto/.
Execute:

```bash
~/apps_wsgi/app/venv/bin/python ~/enable_wsgi_updates.py ~/presotto-update.zip --sha256 0c0567c99e811e05a796a893f5e018b42fa919123933b18c1ef9a8733c01d36e
```

O script valida o SHA-256 do ZIP e do updater, guarda uma copia do updater antigo fora de app/,
substitui somente updater.py de forma atomica e da touch no app.wsgi. Nao muda requirements nem banco.
Recarregue o site, verifique/baixe a versao 1.0.4-compat.4 novamente e clique em Instalar atualizacao.
Se o frontend antigo ainda estiver carregado, recarregue a pagina apos o reinicio.

## Proximas atualizacoes

A instalacao roda dentro da requisicao HTTP, sem subprocessos, cron ou threads de atualizacao.
Aguarde a resposta e o reinicio da hospedagem. Workers antigos bloqueiam requisicoes ate recarregarem.
O painel so confirma a instalacao quando uma instancia da nova versao inicializa.
A instalacao usa uma trava entre workers, revalida os hashes, cria backups de codigo e SQLite
na pasta updates ao lado do banco, e testa o candidato com uma copia do banco antes de aplicar.
Se houver falha capturada ao aplicar, restaura codigo e banco. O rollback posterior preserva os dados
atuais e exige esquema compativel. Uma falha de importacao antes do carregamento do atualizador
pode exigir restauracao assistida a partir dos backups; nao ha supervisor externo nesta hospedagem.

## Validacao e limites

Testes locais simulam WSGI, troca, reinicio, rollback, falhas e preservacao de arquivos.
Sintaxe conferida para Python 3.9; a execucao real da KingHost e seu tempo limite de requisicao
precisam ser validados no primeiro uso. Nao foi acessado nem modificado o servidor do cliente.
Caminhos do front permanecem relativos para /app/.
