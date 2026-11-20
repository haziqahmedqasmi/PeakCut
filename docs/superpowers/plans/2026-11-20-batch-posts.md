# Publicação em lote

**Objetivo:** selecionar clipes prontos e escolher as contas uma vez para todo o lote, conforme a preferência do usuário.

**Abordagem:** aproveitar a seleção existente, abrir uma janela com as contas e enviar um único pedido para a fila existente. Publicar agora mantém o comportamento atual; adicionar à fila respeita o intervalo e o limite diário. Publicações já agendadas ou concluídas são ignoradas.

**Arquivos:** `web/index.html` mantém a interface; `web/pt-br.js` mantém a tradução; `studio/server.py` valida o lote inteiro antes de usar `posting.enqueue`. Testes em `tests/test_posts.py` entram em `tests/run.py`.

**Restrições:** manter o visual existente e as alterações locais; nunca publicar durante testes; obter o usuário da sessão. Selecionar inicialmente as contas do criador ou a única conta conectada. Para vários criadores, selecionar inicialmente apenas contas em comum. A pessoa pode alterar tudo antes de enviar.

- [x] Testar o endpoint ausente, duplicatas, fila, publicação imediata, entradas inválidas e acesso a dados de outra pessoa.
- [x] Adicionar o endpoint com limite de 150 clipes e 20 contas, sem alterações parciais por erro de validação.
- [x] Adicionar a janela de publicação em lote e melhorar a seleção de contas na publicação individual.
- [x] Verificar o fluxo da interface, executar os testes completos e revisar as alterações.

**Verificação:** o Python global não tem SQLAlchemy; usar `.venv/Scripts/python.exe`. A execução inicial encontrou erro de codificação do terminal do Windows; com `PYTHONUTF8=1`, a suíte passou antes e depois das alterações. Os quatro novos testes passaram. A interface foi verificada com jsdom e Chrome em tamanho normal e de celular, incluindo seleção, envio, erro, nova tentativa e navegação para conectar contas. Todos os envios de teste foram simulados. A revisão independente encontrou uma janela que permanecia aberta ao navegar para Redes sociais; o problema foi corrigido e verificado novamente.
