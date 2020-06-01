"""Transformer modules.

References:
    [1]: "Attention Is All You Need", https://arxiv.org/abs/1706.03762
"""
import torch
from torch import nn


def get_pad_mask(query, key, pad_index=0):
    """Computes padding mask from the Query and Key sequences.

    Args:
        query (torch.Tensor): query sequences of shape `[bs, query_len]`
        key (torch.Tensor): key sequences of shape `[bs, key_len]`
        pad_index (int): index used for padding the values

    Returns:
        torch.Tensor: boolean padding mask of shape `[bs, query_len, key_len]`
    """
    bs, seq_len_q = query.shape[:2]
    bs, seq_len_k = key.shape[:2]
    pad_mask = (key == pad_index).unsqueeze(1)
    return pad_mask.expand(bs, seq_len_q, seq_len_k).to(query.device)


def get_autoregressive_mask(seq):
    """Returns autoregressive mask for the decoder inputs.

    Args:
        seq (torch.Tensor): input sequences of shape `[bs, seq_len]`

    Returns:
        torch.bool: boolean mask of shape `[bs, seq_len, seq_len]`
    """
    bs, seq_len = seq.shape[:2]
    autoregressive_mask = torch.triu(torch.ones([bs, seq_len, seq_len]), 1)
    return autoregressive_mask.bool().to(seq.device)


class MultiHeadAttentionLayer(nn.Module):
    """MultiHeadAttentionLayer from "Attention Is All You Need"."""

    def __init__(self, hid_dim=512, n_heads=8, dropout=0.):
        """Initializes MultiHeadAttentionLayer.

        Dimension of one head is `hid_dim` // `n_heads`

        Args:
            hid_dim (int): hidden dimension size
            n_heads (int): number of attention heads
            dropout (float): attention dropout
        """

        super().__init__()

        assert hid_dim % n_heads == 0, "hid_dim must be divisible by n_heads"

        self.hid_dim = hid_dim
        self.n_heads = n_heads
        self.head_dim = hid_dim // n_heads

        # query, key and value linear networks
        self.fc_q = nn.Linear(hid_dim, hid_dim)
        self.fc_k = nn.Linear(hid_dim, hid_dim)
        self.fc_v = nn.Linear(hid_dim, hid_dim)

        # output linear networks
        self.fc_o = nn.Linear(hid_dim, hid_dim)

        # attention dropout
        self.dropout = nn.Dropout(dropout)

        # scale parameter
        self.scale = torch.nn.Parameter(
            torch.sqrt(torch.tensor(self.head_dim, dtype=torch.float32)),
            requires_grad=False
        )

    def forward(self, query, key, value, mask=None):
        """
        Args:
            query (torch.Tensor): queries of shape `[bs, seq_len, hid_dim]`
            key (torch.Tensor): keys of shape `[bs, seq_len, hid_dim]`
            value (torch.Tensor): values of shape `[bs, seq_len, hid_dim]`
            mask (torch.Tensor): boolean mask for padded elements of shape `[bs, seq_len, seq_len]`

        Returns:
            torch.Tensor: multi-head attention tensor of shape `[bs, seq_len, hid_dim]`
        """

        bs, seq_len = query.shape[:2]

        # calculate Q, K, V using corresponding linear networks
        q, k, v = self.fc_q(query), self.fc_k(key), self.fc_v(value)  # shape is [bs, seq_len, hid_dim]

        # prepare Q, K, V for .matmul() or `@` operator
        # shape is [bs, n_heads, seq_len, head_dim]
        q = q.view(bs, seq_len, self.n_heads, self.head_dim).permute(0, 2, 1, 3)
        k = k.view(bs, seq_len, self.n_heads, self.head_dim).permute(0, 2, 3, 1)
        v = v.view(bs, seq_len, self.n_heads, self.head_dim).permute(0, 2, 1, 3)

        # compute energy
        energy = (q @ k) / self.scale  # shape is [bs, n_heads, seq_q_len, seq_k_len]

        if mask is not None:
            # apply mask
            mask = mask.unsqueeze(1).repeat(1, self.n_heads, 1, 1)
            energy = energy.masked_fill(mask, -1e8)

        # apply softmax along the last dim of energy and get the attention weights
        # shape is [bs, n_heads, seq_len, seq_len]
        attention = torch.softmax(energy, dim=-1)
        attention = self.dropout(attention)

        # weight values with calculated attention
        # shape is [bs, n_heads, seq_len, head_dim]
        x = attention @ v

        # squash 1 and 4 dims back
        x = x.permute(0, 2, 1, 3).contiguous()
        x = x.view(bs, -1, self.hid_dim)  # shape is [bs, seq_len, hid_dim]

        # apply output linear layer
        x = self.fc_o(x)

        return x


class PositionwiseFeedforwardLayer(nn.Module):
    """Position-wise Feedforward Layer from "Attention Is All You Need"."""

    def __init__(self, hid_dim=512, pf_dim=2048, dropout=0.):
        """Initializes PositionwiseFeedforwardLayer.

        Args:
            hid_dim (int): hidden dimension size
            pf_dim (int): dimensions of the position-wise layer
            dropout (float): position-wise layer dropout
        """

        super().__init__()

        # linear layers
        self.fc_1 = nn.Linear(hid_dim, pf_dim)
        self.fc_2 = nn.Linear(pf_dim, hid_dim)

        # dropout is applied after the first layer
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        """
        Args:
            x (torch.Tensor): sequences of shape `[bs, seq_len, hid_dim]`

        Returns:
            torch.Tensor: processed sequences of shape `[bs, seq_len, hid_dim]`
        """
        # apply linear layers + dropout
        x = self.dropout(torch.relu(self.fc_1(x)))
        x = self.fc_2(x)

        return x


class EncoderLayer(nn.Module):
    """Encoder Layer of the Vanilla Transformer."""

    def __init__(self, hid_dim=512, n_heads=8, pf_dim=2048, dropout=0.):
        """Initializes EncoderLayer.

        Args:
            hid_dim (int): hidden dimension size
            n_heads (int): number of attention heads
            pf_dim (int): dimensions of the position-wise layer
            dropout (float): attention and position-wise layer dropouts
        """

        super().__init__()

        # self-attention + layer normalization
        self.self_attn = MultiHeadAttentionLayer(hid_dim, n_heads, dropout)
        self.self_attn_ln = nn.LayerNorm(hid_dim)

        # positionwise feedforward layer + layer normalization
        self.pf = PositionwiseFeedforwardLayer(hid_dim, pf_dim, dropout)
        self.pf_ln = nn.LayerNorm(hid_dim)

        # dropout to the outputs of the attention and position-wise feedforward layers
        self.dropout = nn.Dropout(dropout)

    def forward(self, x, input_mask=None):
        """
        Args:
            x (torch.Tensor): input sequences of shape `[bs, seq_len, hid_dim]`
            input_mask (torch.Tensor): boolean mask for padded elements of shape `[bs, seq_len, seq_len]`

        Returns:
            torch.Tensor: processed sequences of shape `[bs, seq_len, hid_dim]`
        """
        ### block 1
        # calculate self-attention + dropout
        attn_out = self.self_attn(x, x, x, mask=input_mask)
        attn_out = self.dropout(attn_out)

        # residual (attention) + attention layer norm
        x = self.self_attn_ln(x + attn_out)

        ### block 2
        # calculate position-wise feedforward + dropout
        ff_out = self.dropout(self.pf(x))

        # residual (position-wise feedforward) + position-wise feedforward layer norm
        x = self.pf_ln(x + ff_out)

        return x


class TransformerEncoder(nn.Module):
    """Multi-layer Transformer Encoder.

    Follows the architecture of Vanilla Transformer Encoder
    from "Attention Is All You Need".

    Modifications:
        - Learned positional embeddings instead of the sinusoidal positional encoding.
    """

    def __init__(self, num_tokens, hid_dim=512, n_layers=6, n_heads=8,
                 pf_dim=2048, dropout=0., pad_index=None, max_len=128):
        """Initializes TransformerEncoder.

        Args:
            num_tokens (int): number of tokens in input sequences
            hid_dim (int): hidden dimension size
            n_layers (int): number of Encoder layers
            n_heads (int): number of attention heads
            pf_dim (int): dimensions of the position-wise layer
            dropout (float): attention and position-wise layer dropouts
            pad_index (int): index used for padding values
            max_len (int): maximum lengths of input sequences.
        """

        super().__init__()

        self.pad_index = pad_index  # if None, don't use masking

        # embeddings
        self.tok_embedding = nn.Embedding(num_tokens, hid_dim)
        self.pos_embedding = nn.Embedding(max_len, hid_dim)
        self.dropout = nn.Dropout(dropout)

        # encoder layers (implemented below)
        self.layers = nn.ModuleList([
            EncoderLayer(hid_dim, n_heads, pf_dim, dropout)
            for _ in range(n_layers)
        ])

        # scale parameter
        self.scale = torch.nn.Parameter(
            torch.sqrt(torch.tensor(hid_dim, dtype=torch.float32)),
            requires_grad=False
        )

        # custom weight initialization
        self.init_weights()

    def init_weights(self):
        for m in self.modules():
            if hasattr(m, 'weight') and m.weight.dim() > 1:
                nn.init.xavier_uniform_(m.weight.data)

    def forward(self, x):
        """
        Args:
            x (torch.Tensor): token sequences of shape `[bs, seq_len]`

        Returns:
            torch.Tensor: encoded sequences of shape `[bs, seq_len, hid_dim]`
        """
        bs, seq_len = x.shape[:2]

        # get token embeddings and scale with self.scale parameter
        tok_emb = self.tok_embedding(x) / self.scale

        # get pos embeddings
        indices = torch.arange(seq_len).repeat(bs, 1).to(x.device)
        pos_emb = self.pos_embedding(indices)

        # sum up token and positional embeddings and apply dropout
        emb = tok_emb + pos_emb
        emb = self.dropout(emb)

        # compute padding mask
        mask = None
        if self.padding_index is not None:
            mask = get_pad_mask(x, x, pad_index=self.pad_index)

        # apply encoder layers one by one; input shape is [bs, seq_len, hid dim]
        x = emb
        for layer in self.layers:
            x = layer(x, input_mask=mask)

        return x


class DecoderLayer(nn.Module):
    """Decoder Layer of the Vanilla Transformer."""

    def __init__(self,
                 hid_dim=512,
                 n_heads=8,
                 pf_dim=2048,
                 dropout=0.):
        """Initializes DecoderLayer.

        Args:
            hid_dim (int): hidden dimension size
            n_heads (int): number of attention heads
            pf_dim (int): dimensions of the position-wise layer
            dropout (float): attention and position-wise layer dropouts
        """

        super().__init__()

        # masked self-attention + layer normalization
        self.self_attn = MultiHeadAttentionLayer(hid_dim, n_heads, dropout)
        self.self_attn_ln = nn.LayerNorm(hid_dim)

        # encoder-attention + layer normalization
        self.enc_attn = MultiHeadAttentionLayer(hid_dim, n_heads, dropout)
        self.enc_attn_ln = nn.LayerNorm(hid_dim)

        # position-wise feedforward layer + layer normalization
        self.pf = PositionwiseFeedforwardLayer(hid_dim, pf_dim, dropout)
        self.pf_ln = nn.LayerNorm(hid_dim)

        # attention and position-wise feedforward layer dropouts
        self.dropout = nn.Dropout(dropout)

    def forward(self, x, enc_out, input_mask=None, enc_mask=None):
        """
        Args:
            x (torch.Tensor): input sequences of shape `[bs, seq_len, hid_dim]`
            enc_out (torch.Tensor): encoder outputs of shape `[bs, seq_len, hid_dim]`
            input_mask (torch.Tensor): masked self-attention + padding mask of shape `[bs, seq_len, seq_len]`
            enc_mask  (torch.Tensor): encoder outputs padding mask of shape `[bs, seq_len, seq_len]`

        Returns:
            torch.Tensor: processed sequences of shape `[bs, seq_len, hid_dim]`
        """
        ### block 1
        # self-attention + dropout
        attn_out = self.self_attn(x, x, x, mask=input_mask)
        attn_out = self.dropout(attn_out)

        # residual (attention) + attention layer norm
        x = self.self_attn_ln(x + attn_out)

        ### block 2
        # encoder-attention + dropout
        attn_out = self.enc_attn(x, enc_out, enc_out, mask=enc_mask)
        attn_out = self.dropout(attn_out)

        # residual (attention) + attention layer norm
        x = self.enc_attn_ln(x + attn_out)

        ### block 2
        # positionwise feedforward + dropout
        ff_out = self.dropout(self.pf(x))

        # residual (positionwise feedforward) + positionwise feedforward layer norm
        x = self.pf_ln(x + ff_out)

        return x


class TransformerDecoder(nn.Module):
    """Multi-layer Transformer Decoder.

    Follows the architecture of Vanilla Transformer Decoder from "Attention Is All You Need".

    Outputs scores for tokens in the target sequence.

    Modifications:
        - Learned positional embeddings instead of the sinusoidal positional encoding.
        - Allows passing as input image embedding vector which is prepended to
        the token embeddings.
    """

    def __init__(self, num_tokens, hid_dim=512, n_layers=6, n_heads=8,
                 pf_dim=2048, dropout=0., pad_index=None, max_len=128):
        """Initializes TransformerDecoder.

        Args:
            num_tokens (int): number of tokens in input sequences
            hid_dim (int): hidden dimension size
            n_layers (int): number of Decoder layers
            n_heads (int): number of attention heads
            pf_dim (int): dimensions of the position-wise layer
            dropout (float): attention and position-wise layer dropouts
            pad_index (int): index used for padding values in input sequences
            max_len (int): maximum lengths of input sequences.
        """

        super().__init__()

        self.pad_index = pad_index  # if None, don't use masking

        # embeddings
        self.tok_embedding = nn.Embedding(num_tokens, hid_dim)
        self.pos_embedding = nn.Embedding(max_len, hid_dim)
        self.dropout = nn.Dropout(dropout)

        # decoder layers (implemented below)
        self.layers = nn.ModuleList([
            DecoderLayer(hid_dim, n_heads, pf_dim, dropout)
            for _ in range(n_layers)
        ])

        # scale parameter
        self.scale = torch.nn.Parameter(
            torch.sqrt(torch.tensor(hid_dim, dtype=torch.float32)),
            requires_grad=False
        )

        # output layer
        self.classifier = nn.Linear(hid_dim, num_tokens)

        # custom weight initialization
        self.init_weights()

    def init_weights(self):
        for m in self.modules():
            if hasattr(m, 'weight') and m.weight.dim() > 1:
                nn.init.xavier_uniform_(m.weight.data)

    def forward(self, x, enc_out, image_emb=None):
        """
        Args:
            x (torch.Tensor): token sequences of shape `[bs, seq_len]`
            enc_out (torch.Tensor): encoder outputs of shape `[bs, seq_len, hid_dim]`
            image_emb (torch.Tensor, optional): image embeddings of shape `[bs, hid_dim]`

        Returns:
            torch.Tensor: decoded sequences of shape `[bs, seq_len, num_tokens]`
        """
        device = x.device
        bs, dec_seq_len = x.shape[:2]
        enc_seq_len, hid_dim = enc_out.shape[1:3]

        if image_emb is not None:
            dec_seq_len += 1

        # pad input and encoder outputs to the same seq_len
        seq_len = max(dec_seq_len, enc_seq_len)
        x = torch.cat([x, self.pad_index * torch.ones(bs, seq_len - dec_seq_len).long().to(device)], dim=1)
        enc_out = torch.cat([enc_out, torch.zeros(bs, seq_len - enc_seq_len, hid_dim).to(device)], dim=1)

        # get token embeddings
        tok_emb = self.tok_embedding(x)

        # add image embedding:
        if image_emb is not None:
            tok_emb = torch.cat((image_emb.unsqueeze(1), tok_emb), 1)

        # scale token embeddings with self.scale parameter
        tok_emb = tok_emb / self.scale

        # get pos embeddings
        indices = torch.arange(seq_len).repeat(bs, 1).to(device)
        pos_emb = self.pos_embedding(indices)

        # sum up token and positional embeddings and apply dropout
        emb = tok_emb + pos_emb
        emb = self.dropout(emb)

        # compute decoder input mask
        if image_emb is not None:
            x = torch.cat([torch.ones(bs, 1).long().to(device), x], dim=1)
        pad_mask = get_pad_mask(x, x, pad_index=self.pad_index)
        autoregr_mask = get_autoregressive_mask(x)
        input_mask = pad_mask | autoregr_mask

        # compute encoder output mask
        enc_inp_mask = (enc_out != 0.).all(dim=-1).long()
        enc_mask = get_pad_mask(x, enc_inp_mask, pad_index=self.pad_index)

        # apply encoder layers one by one; input shape is [bs, seq_len, hid dim]
        x = emb
        for layer in self.layers:
            x = layer(x, enc_out, input_mask=input_mask, enc_mask=enc_mask)

        out = self.classifier(x)

        return out

    def inference_step(self, inputs, hidden=None):
        inputs = self.embedding(inputs)
        outputs, hidden = self.lstm(inputs, hidden)
        outputs = self.linear(outputs.squeeze(1))
        return outputs, hidden