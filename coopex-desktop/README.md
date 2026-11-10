# COOPEX Desktop — desenvolvimento isolado

**Estado: protótipo inicial; não implantar em produção.**

Esta branch foi aberta para construir o aplicativo Windows sem alterar o HTML, CSS, rotas ou regras financeiras existentes do COOPEX no Render.

## Arquitetura
- Desktop Windows: WebView2 com o mesmo site Flask online (visual e funções originais).
- Offline: SQLite local, com dados por usuário, cifrados antes de aceitar dados reais.
- Sincronização: endpoint Flask autenticado, chave UUID idempotente por operação, versões e política de conflitos.
- Estados exibidos: **Salvo neste computador**, **Pendente de sincronização**, **Confirmado no servidor**, **Conflito**.
- Operações que exigem autoridade online: Pix, boleto, NFS-e, ordenação/entrada/saída na fila, escala compartilhada e GPS ao vivo. Nunca confirmar essas ações ficticiamente offline.
- Segurança: sem guardar senhas em texto puro; expirar sessões; revogar acesso e limpar caches por cooperado/estabelecimento.

## Protótipo criado em 10/11/2026
Existe um pacote ZIP de desenvolvimento entregue na conversa, contendo `main.py`, `offline_store.py`, `offline.html`, `build_windows.ps1` e três testes automatizados que passaram.

Este primeiro pacote **não tem sincronização automática real** e **não é o instalador final**. O site online preserva a interface existente; a tela de contingência ainda não replica todas as telas nem executa operações de negócio.

## Critérios de aceite antes de disponibilizar para todos
1. Mesmo design, menus e funções da versão online.
2. Leitura offline dos dados autorizados, marcados com última sincronização.
3. Operações offline permitidas em fila local cifrada, com reenvio seguro e idempotente ao restabelecer rede/Render.
4. Sem perda, duplicação ou sobrescrita de dados entre PCs.
5. Testes de conectividade, crash, sessão expirada, concorrência, revogação, atualização e alta carga.
6. Instalador .exe testado e assinado; rollback disponível.

**Não misturar esta branch experimental ao deploy atual até os testes e revisão.**
