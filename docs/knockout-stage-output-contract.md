# Contrato de Saída: Mata-Mata

Este contrato vale a partir do momento em que o Brasil entra no mata-mata.

## Temporalidade

- Fases já concluídas deixam de aparecer como caminho futuro.
- Resultados concluídos entram como histórico narrativo antes do caminho restante.
- O próximo jogo deve ser o primeiro confronto futuro do Brasil, usando data do bundle.
- No mata-mata, o cabeçalho usa `Brasil passa / adversário passa`; empate fica sempre `0`.

## Caminho

- O bloco `O CAMINHO ATÉ O HEXA` começa na fase atual ou futura.
- Se o Brasil já venceu os 16 avos, o caminho começa em `OITAVAS`.
- O contexto histórico deve dizer explicitamente como o Brasil chegou ali, por exemplo:
  `Avançou para as oitavas de final com a vitória nos 16 avos sobre o Japão por 2x1.`
- Cruzamento travado usa `Definido` e `100% de chance desse cruzamento`.

## Resumo da Caminhada

- As probabilidades de chegada por fase devem vir do Monte Carlo condicionado aos resultados já conhecidos.
- A chance de chegar às quartas, quando o próximo jogo é oitavas, é a chance de passar das oitavas.
- Portanto, se `Brasil x Noruega` está em `74,1%`, o resumo deve dizer `quartas em 74%`, não um valor divergente de pós-debate.
- O título continua sendo o percentual final publicado pelo funil do run.

## Bastidores

- Bastidor não pode ser coreografia de sala, liderança genérica ou frase administrativa.
- Bastidor precisa expor raciocínio substantivo do audit: variável, threshold, trade-off ou hipótese testada.
- Exemplos desejáveis:
  - `Para subir o Hexa de 11,7% para 12,7%, Brasil x Noruega teria que saltar de 74,1% para 80,4%.`
  - `A sala criou matriz de gatilhos: odds, lesão, escalação ou rating precisam mover Brasil x Noruega/Inglaterra em 3 p.p.`
  - `Haaland/tornozelo deve virar teste de sensibilidade, não palpite solto.`
- É proibido converter percentuais de naturezas diferentes como se fossem ajuste do mesmo evento, por exemplo `Hexa 11,7% caiu para Brasil x Noruega 74,1%`.

## Infográfico

- O infográfico deve usar o mesmo contrato temporal do post.
- O card do último run deve mostrar o próximo confronto futuro, não o primeiro mata-mata antigo.
- O insight de cruzamento travado deve apontar para o próximo jogo travado relevante.
- Ranking de modelos, influência por run e índice de acerto permanecem, mas não podem carregar confronto já resolvido como se fosse atual.

