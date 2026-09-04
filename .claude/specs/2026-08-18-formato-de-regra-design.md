# rules-by-path — formato de regra simplificado

Substitui a primeira versão desta spec (mesmo dia). Aquela desenhava uma
moldura elaborada — preâmbulo com nonce, cabeçalho JSON com proveniência,
lembrete resumido. A decisão do Pedro, depois de percorrermos o mecanismo passo
a passo, foi cortar tudo isso: **injetar a regra crua**.

O que derrubou a moldura foi uma pergunta dele: *em que um arquivo de regra
malicioso difere de um `CLAUDE.md` malicioso no mesmo repositório?* Não difere —
e o `CLAUDE.md` chega ao modelo com autoridade declarada maior e nenhuma
autenticação. A proteção que tínhamos construído defendia sobretudo uma
superfície que nós mesmos criamos: conteúdo forjando `scope: global` para
parecer mais confiável que o bloco vizinho. Sem proveniência emitida, esse
ataque não tem o que ganhar.

---

## 1. O formato

**Arquivo de regra** — frontmatter com dois campos, um obrigatório:

```markdown
---
glob: src/Application/**/*Handler.cs
remember_after: 30k
---
Antes de adicionar um método a um handler, leia `Application/Common/BaseHandler.cs`.

Se o comportamento couber lá, herde de BaseHandler em vez de reimplementar.
Handlers que não herdam já duplicaram método da base.
```

- `glob:` — obrigatório. Continua aceitando lista (`globs:`) e o cap de 16.
- `remember_after:` — opcional. `30k`/`30000`/`1M` = tokens de contexto;
  `25 calls` = chamadas de ferramenta; `never` = injeta uma vez e nunca
  relembra. Ausente = default (§3).

O reforço só é avaliado **quando o glob casa de novo**: distância percorrida é
condição necessária, não suficiente. Regra de pasta que ninguém reabre nunca é
repetida.

**Texto injetado** — é isto e nada mais:

```
<rules-by-path>
Antes de adicionar um método a um handler, leia `Application/Common/BaseHandler.cs`.

Se o comportamento couber lá, herde de BaseHandler em vez de reimplementar.
Handlers que não herdam já duplicaram método da base.
---
Negócio: pedido não pode ser cancelado depois de faturado.
</rules-by-path>
```

- as tags delimitam o bloco; `---` numa linha separa duas regras. Linha em
  branco não serve como separador — corpo de regra tem linha em branco.
- ordem: global primeiro, depois projeto da raiz para a pasta do arquivo.
- **o lembrete é o texto inteiro reenviado.** Não há forma curta: sem cabeçalho
  não há como marcar "isto é um fragmento". Consequência de projeto: regra curta
  não é estilo, é o que torna o reforço barato.

Custo, medido no exemplo acima com uma regra: **248 caracteres, dos quais 33 de
moldura**. Hoje são 933 na injeção e 801 em cada lembrete. Numa sessão com uma
injeção e 20 lembretes: 16.953 → 5.208 caracteres, 69% a menos.

---

## 2. O tipo da regra não é injetado

`Business_`/`Architecture_`/`Convention_` são **obrigatórios no nome do
arquivo** — `Type_o-que-afirma.md` — para o humano organizar e para o `list`
agrupar. Eles não chegam ao modelo. A skill `manage` manda **perguntar ao
usuário** quando o tipo não for inferível: é um juízo sobre o que custa violar a
regra, e quem sabe isso é quem é dono do código. O `validate` emite uma **nota**
(nunca erro) para nome fora do padrão — o hook nunca recusa uma regra por causa
do nome dela.

Motivo: o rótulo só muda comportamento quando duas regras conflitam e algo
precisa ser sacrificado — situação rara. O que muda comportamento é a frase da
regra. Quem quiser o tipo visível escreve na frase, e decide caso a caso se vale
os dez caracteres:

```
Negócio: pedido não pode ser cancelado depois de faturado.
```

Zero campo novo, zero código, zero conjunto fechado para o hook conhecer.

---

## 3. Reforço

O estado por sessão continua existindo — é a única memória do hook, que não
enxerga a conversa. Ele passa a guardar, além do que já guarda, **o tamanho da
janela de contexto** e a unidade em uso.

```json
{"calls": 41,
 "seen": {"<escopo>::<nome>::<sha256 do corpo>": [41, 312845]}}
```

Guarda as **duas** medidas por regra — número da chamada e tokens de contexto —
porque cada regra escolhe sua unidade: uma pode pedir 30k tokens e outra 25
chamadas na mesma sessão.

A chave inclui o hash do corpo, então **regra editada conta como regra nova** e
volta inteira — sem isso, corrigir uma regra no meio da sessão não teria efeito.

Como o contexto é medido, em ordem:

1. `transcript_path` legível e com `usage` parseável → soma do último `usage`
   (`input + cache_creation + cache_read + output`). `seek` nos últimos ~64 KB,
   custo constante: o transcript chega a alguns MB e ler inteiro a cada chamada
   mataria os ~100ms do hook.
2. Senão → conta chamadas, como hoje.

Regra configurada em tokens quando só há contagem de chamadas **cai para o
default em chamadas**, e o `validate`/`status` diz em que modo está rodando. Não
há conversão entre as unidades: não existe taxa fiel de tokens por chamada, e
fingir precisão é pior que perdê-la.

Percentual da janela de contexto foi considerado e **descartado**: a janela é o
único número que o harness não reporta — nem no payload do hook nem no
transcript (verificado) —, e uma tabela modelo→janela dentro do plugin
envelheceria a cada modelo novo. Como o `usage` dá a contagem absoluta de
tokens, o parâmetro absoluto (`30k`) faz o mesmo trabalho sem depender de
configuração extra. Pela mesma razão o estado não guarda `window` nem `model`:
nada os consumiria.

Defaults: `30k` tokens quando mensurável, `25 calls` quando não. Valor abaixo de
1000 sem unidade é tratado como resquício da era de chamadas — `validate` avisa
e o hook usa o default.

Por que tokens e não chamadas: a métrica atual está invertida. Uma sessão que lê
três arquivos gigantes queima 200k tokens em 3 chamadas e não recebe lembrete
nenhum; uma que faz 50 greps minúsculos queima 20k em 50 chamadas e recebe dois.

Compactação encolhe o contexto e torna o delta negativo — é exatamente aí que o
`SessionStart(compact)` já zera o estado. Os dois mecanismos casam.

---

## 4. Conteúdo da regra

Esta parte é independente da moldura e é a que ataca a dor real: o harness não
percebe que um desenvolvimento correlacionado já existe (criou
`Application/Enum/` ao lado de `Application/Enums/`; escreveu método num handler
em vez de herdar do `BaseHandler`).

**A doutrina atual da skill proíbe a regra que teria evitado isso.** Ela diz que
regra é *"uma restrição que muda o que você faz, não conhecimento que você
obteria lendo o código"* — e "enums moram em `Application/Enums`" é,
literalmente, conhecimento obtenível lendo o código. A fronteira passa a ser
outra:

| Fora | Dentro |
|---|---|
| **Descritivo** — como o sistema funciona | **Prescritivo de colocação** — onde coisa nova mora, de que herda, o que reusar antes de criar |

**O corolário é o ponto mais importante desta seção: a regra anti-duplicação
precisa de glob na área *errada*.** Uma regra com `glob:
src/Application/Enums/**` nunca dispara quando o agente cria
`Application/Enum/` — ele nunca toca o caminho canônico, esse *é* o bug. O glob
tem de ser `src/Application/**`. Idem para o `BaseHandler`: a regra tem de casar
com **qualquer** handler, inclusive o que não herda dele.

Forma do texto: procedimento ancorado no que é estável, com o **modo de falha
nomeado** — é o que faz o modelo reconhecer a situação em que está. Inventário
("enums moram em X") custa zero e apodrece em silêncio a cada refactor.

E **uma restrição por arquivo**: o bloco é a concatenação dos corpos, então
regra longa é reenviada longa a cada lembrete.

---

## 5. A defesa que fica, e ela é grátis

Nenhuma delas acrescenta texto injetado.

- **Defang do corpo** — dentro do corpo de uma regra, viram inertes (espaço de
  largura zero após o primeiro caractere, visualmente idênticos):
  `<rules-by-path`, `</rules-by-path`, uma linha que seja exatamente `---`, e os
  marcadores do próprio harness: `<system-reminder`, `</system-reminder`,
  `<function_calls`, `<function_results`.

  Os do harness são os que mais importam. Passar-se por regra dá a autoridade de
  uma regra; passar-se pelo harness dá a autoridade máxima do contexto — é onde
  o `CLAUDE.md` entra dizendo "*these instructions OVERRIDE any default
  behavior*". São escaladas diferentes e só a segunda é grave.
- **Contenção** — o diretório de regras tem de morar fisicamente dentro do
  escopo (symlink apontando para fora é ignorado), o arquivo não pode escapar
  dele, e o diretório não pode ser world-writable nem de outro usuário.
- **Caps** — 4.000 chars por regra (acima disso trunca e anexa uma linha
  dizendo), 24.000 por injeção, 256 regras por escopo, 16 globs por regra,
  2s de orçamento total para casar globs. Todos fail-open.
- **Autoria pela CLI** — `add`/`update` recusam sobrescrever markdown que não é
  regra; a hardening recomendada deny-lista o diretório para leitura direta.

---

## 6. O que sai

| Sai | Por quê |
|---|---|
| Preâmbulo (525 chars/chamada) | Explicava um esquema de autenticação que deixa de existir |
| Nonce | Só valia para autenticar proveniência que não é mais emitida |
| Cabeçalho JSON | Idem |
| `name`, `glob`, `scope` emitidos | Proveniência sobre a qual nada era afirmado; `which` responde sob demanda |
| `reminder`, `truncated` | O primeiro nada mudava; o segundo já era duplicado pela linha anexada ao corpo |
| Contagem `i/N` | Só fazia sentido com o nonce |
| `summarize` | Lembrete passa a ser o texto inteiro |
| `sanitize_label` | Não há mais label emitido |
| Política de `CLAUDE.md` aninhado | Decisão do Pedro: pode deixar criar. Sai o `deny`, saem as menções no README e nos SKILLs |
| Sugestão de `add` na saída do `which` | Ruído |

Descartado no caminho, com o motivo: **GUID no lembrete** (o modelo não tem
tabela de consulta — pagaria tokens por um ponteiro que não consegue
desreferenciar); **pastas por tipo** (a descoberta é plana, uma regra em
subpasta nunca carregaria, e em silêncio); **campo `kind:`** (§2); **estimativa
de contexto pelo tamanho dos arquivos vistos** (o hook só enxerga entradas de
ferramenta, não resultados — erraria feio em sessão dominada por saída de bash).

---

## 7. Superfície de mudança

**`hooks/rules-by-path.py`** — `build_context` encolhe para as duas tags e o
separador; somem `secrets`, `summarize`, `sanitize_label`, `is_nested_claude_md`
e `NESTED_CLAUDE_MD_REASON`; `neutralize` troca de lista de tokens; nova
`context_size(payload)`; `open_state`/`save_state` ganham `unit` e `window`;
`find_scopes` deixa de parar no `.git` (§8); `derive_rule_name` vira função
total.

**`scripts/rules-by-path-admin.py`** — `remember_after` no `render_rule` e no
`validate` (sintaxe, unidade, valor suspeito); `which` sem a sugestão de `add`;
notas do `validate` sobre regra longa e primeira linha continuam, sem a doutrina
de "primeira linha é a única que sobrevive".

**Testes** — `test_security.py`: reescrever as regressões de forja de cabeçalho
e de `scope` (a superfície some com o campo), manter as de contenção, symlink,
dono e caps. `test_hook.py`: novo formato de saída, reforço por tokens,
fallback, `never`. `test_admin.py`: `remember_after`, derivação total.

**Docs** — `skills/manage/SKILL.md` (doutrina do §4, formato do §1, sintaxe do
`remember_after`), `README.md`, `commands/status.md` (reporta o modo de medição),
`CHANGELOG.md` (ainda devendo a entrada da `0.2.0`).

---

## 8. Em aberto

- ~~Onde a subida de escopos termina.~~ **Decidido: vai até a raiz do
  filesystem.** A subida deixa de parar no primeiro `.git` e coleta todos os
  `.claude/rules-by-path` do caminho — resolve o submódulo, que hoje não recebe
  nada porque `.git` ali é um arquivo e `os.path.exists` casa igual. O que se
  aceita em troca: um diretório que o usuário não controla passa a poder injetar
  em toda sessão abaixo dele; o filtro de dono e permissão do `usable_scope`
  (recusa dono diferente e world-writable) é o que sobra dessa defesa.
  Detalhe de implementação: o cap `MAX_SCOPES` hoje "reserva a raiz do
  repositório" quando estoura — sem o conceito de raiz de repo, a política de
  descarte precisa ser reescrita (proposta: manter os mais próximos do arquivo e
  o mais alto da cadeia).
- **Tipo injetado.** §2 decide que não vai. Reabrir se o uso mostrar conflito
  entre regras com frequência.
- ~~Experimento pendente sobre a moldura do harness.~~ **Feito em 18/08, ao
  vivo nesta sessão.** Regra criada pela CLI, `Read` num arquivo que ela cobre,
  regra removida em seguida. O que chegou ao modelo:

      PreToolUse:Read hook additional context: [rules-by-path] Authentic rule blocks…

  Dois resultados. **(a)** O harness rotula com evento *e* ferramenta — "isto
  veio de um hook" já vem de graça, e o preâmbulo pagava por isso. **(b)** As
  tags `<rules-by-path>` ficam: na mesma mensagem, logo depois do nosso bloco,
  chegou outro documento injetado por outro mecanismo
  (`Contents of /opt/shared/.claude/rules/python-conventions.md`, ~1,4 KB,
  disparado por ler um `.py`). Sem tag de fechamento não há como saber onde as
  regras terminam.
- **Segundo injetor rodando em paralelo.** O achado (b) acima expõe um mecanismo
  fora do plugin que injeta convenções por tipo de arquivo. Não investigado.
  Vale saber se duplica o trabalho do rules-by-path no setup do Pedro.
