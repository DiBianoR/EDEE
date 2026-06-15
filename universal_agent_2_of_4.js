{
  "nodes": [
    {
      "parameters": {
        "conditions": {
          "options": {
            "caseSensitive": true,
            "leftValue": "",
            "typeValidation": "strict",
            "version": 2
          },
          "conditions": [
            {
              "id": "c1",
              "leftValue": "={{ $json._skip_api }}",
              "rightValue": true,
              "operator": {
                "type": "boolean",
                "operation": "equals"
              }
            }
          ],
          "combinator": "and"
        },
        "options": {}
      },
      "type": "n8n-nodes-base.if",
      "typeVersion": 2.2,
      "position": [
        432,
        0
      ],
      "id": "3e1e62e4-abab-4328-8191-39c61f002b25",
      "name": "Skip API Call?"
    },
    {
      "parameters": {
        "method": "POST",
        "url": "={{ $json._model_url }}",
        "sendQuery": true,
        "queryParameters": {
          "parameters": [
            {
              "name": "key",
              "value": "={{ $json._api_key }}"
            }
          ]
        },
        "sendHeaders": true,
        "headerParameters": {
          "parameters": [
            {
              "name": "Content-Type",
              "value": "application/json"
            }
          ]
        },
        "sendBody": true,
        "specifyBody": "json",
        "jsonBody": "={{ $json._request_body }}",
        "options": {
          "timeout": 180000
        }
      },
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [
        656,
        192
      ],
      "id": "83a14107-a84e-4a27-8cd4-5ffc105a2041",
      "name": "2. API Call",
      "retryOnFail": true,
      "maxTries": 3,
      "waitBetweenTries": 2000
    }
  ],
  "connections": {
    "Skip API Call?": {
      "main": [
        [],
        [
          {
            "node": "2. API Call",
            "type": "main",
            "index": 0
          }
        ]
      ]
    },
    "2. API Call": {
      "main": [
        []
      ]
    }
  },
  "pinData": {},
  "meta": {
    "instanceId": "df2a3209410ed788eacffb4d1872acd7fcd84c9b8c68de9775fcdebdbc87d329"
  }
}