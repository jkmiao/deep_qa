"""
Entailment layers take three inputs, and combine them in some way to get to T/F.  They must end
with a softmax output over two dimensions.

Inputs:
    sentence_encoding
    current_memory
    attended_background_knowledge

We are trying to decide whether sentence_encoding is entailed by some background knowledge.
attended_background_knowledge and current_memory are two different vectors that encode the state of
reasoning over this background knowledge, which the entailment model can choose to use as desired.
"""

from typing import Any, Dict

from keras import backend as K
from keras import initializations, activations
from keras.layers import Dense, Layer, TimeDistributed

from ..common.tensors import switch, masked_softmax, tile_vector

class TrueFalseEntailmentModel:
    """
    This entailment model assumes that we merge the three inputs in some way at the beginning into
    a single vector, then pass that vector through an MLP.  How we actually merge the three inputs
    is specified by one of the combiners below.
    """
    def __init__(self, params: Dict[str, Any]):
        self.num_hidden_layers = params.pop('num_hidden_layers', 1)
        self.hidden_layer_width = params.pop('hidden_layer_width', 50)
        self.hidden_layer_activation = params.pop('hidden_layer_activation', 'relu')

        self.hidden_layers = None
        self.softmax_layer = None
        self._init_layers()

    # TODO(matt): it would make sense to put the hidden layers and the score layer into a
    # superclass, as this method is the same for both the TrueFalseEntailmentModel and the
    # MultipleChoiceEntailmentModel.  The QuestionAnswerEntailmentModel throws a bit of a wrench
    # into this, though, so it needs some careful thought.
    def _init_layers(self):
        self.hidden_layers = []
        for i in range(self.num_hidden_layers):
            self.hidden_layers.append(Dense(output_dim=self.hidden_layer_width,
                                            activation=self.hidden_layer_activation,
                                            name='entailment_hidden_layer_%d' % i))
        self.score_layer = Dense(output_dim=2, activation='softmax', name='entailment_softmax')

    def classify(self, combined_input):
        """
        Here we take the combined entailment input and decide whether it is true or false.  The
        combined input has shape (batch_size, combined_input_dim).  We do not know what
        combined_input_dim is, as it depends on the combiner.
        """
        hidden_input = combined_input
        for layer in self.hidden_layers:
            hidden_input = layer(hidden_input)
        softmax_output = self.score_layer(hidden_input)
        return softmax_output


class AnswerSimilaritySoftmax(Layer):
    '''
    This layer takes a list of two tensors: projected_entailment_input and encoded_answers, and computes
    a softmax over the options based on how similar they are to the input. It first tiles the entailment_input
    to compute an efficient dot product (followed by a sum) with the encoded answers.
    Input shapes:
        projected_input: (batch_size, answer_dim)
        encoded_answers: (batch_size, num_options, answer_dim)
    Output shape: (batch_size, num_options)
    '''
    def __init__(self, **kwargs):
        self.supports_masking = True
        super(AnswerSimilaritySoftmax, self).__init__(**kwargs)

    def compute_mask(self, inputs, mask=None):
        # pylint: disable=unused-argument
        # We do not need a mask beyond this layer.
        return None

    def get_output_shape_for(self, input_shapes):
        return (input_shapes[1][0], input_shapes[1][1])

    def call(self, inputs, mask=None):
        projected_input, encoded_answers = inputs
        if mask is not None:
            # The projected input is not expected to have a mask, since it is the output of a Dense layer.
            answers_mask = mask[1]
            if K.ndim(answers_mask) < K.ndim(encoded_answers):
                # To use switch, we need the answer_mask to be the same size as the encoded_answers.
                # Therefore we expand it and multiply by ones in the shape that we need.
                answers_mask = K.expand_dims(answers_mask) * K.cast(K.ones_like(encoded_answers), "uint8")
            encoded_answers = switch(answers_mask, encoded_answers, K.zeros_like(encoded_answers))
        # (batch_size, answer_dim) --> (batch_size, num_options, answer_dim)
        tiled_input = tile_vector(projected_input, encoded_answers)
        softmax_output = masked_softmax(K.sum(encoded_answers * tiled_input, axis=2), mask[1])
        return softmax_output


class QuestionAnswerEntailmentModel:
    """
    In addition to the three combined inputs mentioned at the top of this file, this entailment
    model also takes a list of answer encodings.  Instead of going to a final softmax for the
    combined input, as the TrueFalseEntailmentModel does, this model projects the combined input
    into the same dimension as the answer encoding, does a dot product between the combined input
    encoding and the answer encoding, and does a final softmax over those similar scores.
    """
    def __init__(self, params: Dict[str, Any]):
        self.num_hidden_layers = params.pop('num_hidden_layers', 1)
        self.hidden_layer_width = params.pop('hidden_layer_width', 50)
        self.hidden_layer_activation = params.pop('hidden_layer_activation', 'relu')
        self.answer_dim = params.pop('answer_dim')

        self.hidden_layers = None
        self.softmax_layer = None
        self.projection_layer = None
        self.tile_layer = None
        self.similarity_layer = None
        self._init_layers()

    def _init_layers(self):
        self.hidden_layers = []
        for i in range(self.num_hidden_layers):
            self.hidden_layers.append(Dense(output_dim=self.hidden_layer_width,
                                            activation=self.hidden_layer_activation,
                                            name='entailment_hidden_layer_%d' % i))
        self.projection_layer = Dense(output_dim=self.answer_dim,
                                      activation='linear',
                                      name='entailment_projection')

    def classify(self, combined_input, encoded_answers):
        """
        Here we take the combined entailment input, do a little more processing on it, then decide
        which answer it is closest to.  Specifically, we pass the combined input through a few
        hidden layers, then use a linear projection to get it into the same dimension as the answer
        encoding.  Then we do a dot product between the combined input and each of the answer
        options, and pass those values through a softmax.

        The combined input has shape (batch_size, combined_input_dim).  encoded_answers has shape
        (batch_size, num_options, answer_dim).
        """
        hidden_input = combined_input
        for layer in self.hidden_layers:
            hidden_input = layer(hidden_input)
        projected_input = self.projection_layer(hidden_input)

        # Note that this layer has no parameters, so it doesn't need to be put into self._init_layers().
        softmax_output = AnswerSimilaritySoftmax(name='answer_similarity_softmax')([projected_input,
                                                                                    encoded_answers])
        return softmax_output


class AnswerOptionSoftmax(Layer):
    '''
    This layer accepts a tensor of scores of shape (batch_size, num_options, 1), and calculates the softmax
    of those scores over num_options. The reason we have a final dimension of length 1 is because the input
    is expected to be the output of a TimeDistributed scoring layer. See MultipleChoiceEntailmentModel.classify().
    This could have been a lambda layer, except that we need to accept masked input, which Lambda doesn't.
    '''
    def __init__(self, **kwargs):
        self.supports_masking = True
        super(AnswerOptionSoftmax, self).__init__(**kwargs)

    def compute_mask(self, inputs, mask=None):
        # pylint: disable=unused-argument
        # We do not need a mask beyond this layer.
        return None

    def get_output_shape_for(self, input_shape):
        return (input_shape[0], input_shape[1])

    def call(self, x, mask=None):
        # input shape: (batch_size, num_options, 1)
        squeezed_x = K.squeeze(x, axis=2)  # (batch_size, num_options)
        return masked_softmax(squeezed_x, mask)


class MultipleChoiceEntailmentModel:
    """
    This entailment model assumes that we merge the three inputs in some way at the beginning into
    a single vector, then pass that vector through an MLP, once for each of the multiple choices,
    then have a final softmax over answer options.
    """
    def __init__(self, params: Dict[str, Any]):
        self.num_hidden_layers = params.pop('num_hidden_layers', 1)
        self.hidden_layer_width = params.pop('hidden_layer_width', 50)
        self.hidden_layer_activation = params.pop('hidden_layer_activation', 'relu')

        self.hidden_layers = None
        self.score_layer = None
        self._init_layers()

    def _init_layers(self):
        self.hidden_layers = []
        for i in range(self.num_hidden_layers):
            self.hidden_layers.append(Dense(output_dim=self.hidden_layer_width,
                                            activation=self.hidden_layer_activation,
                                            name='entailment_hidden_layer_%d' % i))
        self.score_layer = Dense(output_dim=1, activation='sigmoid')

    def classify(self, combined_input):
        """
        Here we take the combined entailment input for each option, decide whether it is true or
        false, then do a final softmax over the true/false scores for each option.

        The combined input has shape (batch_size, num_options, combined_input_dim).
        """
        # pylint: disable=redefined-variable-type
        hidden_input = combined_input
        for layer in self.hidden_layers:
            hidden_input = TimeDistributed(layer, name=layer.name)(hidden_input)

        # (batch_size, num_options, 1)
        scores = TimeDistributed(self.score_layer, name='entailment_scorer')(hidden_input)

        # This layer has no parameters, so it doesn't need to go into self._init_layers().
        softmax_layer = AnswerOptionSoftmax(name='answer_option_softmax')
        softmax_output = softmax_layer(scores)
        return softmax_output


class DecomposableAttentionEntailment(Layer):
    '''
    This layer is a reimplementation of the entailment algorithm described in the following paper:
    "A Decomposable Attention Model for Natural Language Inference", Parikh et al., 2016.
    The algorithm has three main steps:
    1) Attend: Compute dot products between all pairs of projections of words in the hypothesis and the premise,
        normalize those dot products to use them to align each word in premise to a phrase in the hypothesis and
        vice-versa. These alignments are then used to summarize the aligned phrase in the other sentence as a
        weighted sum. The initial word projections are computed using a feed forward NN, F.
    2) Compare: Pass a concatenation of each word in the premise and the summary of its aligned phrase in the
        hypothesis through a feed forward NN, G, to get a projected comparison. Do the same with the hypothesis
        and the aligned phrase from the premise.
    3) Aggregate: Sum over the comparisons to get a single vector each for premise-hypothesis comparison, and
        hypothesis-premise comparison. Ass them through a third feef forward NN, H to get the entailment decision.

    At this point this doesn't quite fit into the setup we have here because the model doesn't
    operate on the encoded sentence representations, but instead consumes the word level representations.
    TODO(pradeep): Make this work with the memory network eventually.
    '''
    def __init__(self, params: Dict[str, Any]):
        self.num_hidden_layers = params.pop('num_hidden_layers', 1)
        self.hidden_layer_width = params.pop('hidden_layer_width', 50)
        self.hidden_layer_activation = params.pop('hidden_layer_activation', 'relu')
        self.init = initializations.get(params.pop('init', 'uniform'))
        self.supports_masking = True
        super(DecomposableAttentionEntailment, self).__init__(**params)

    def build(self, input_shape):
        '''
        This model has three feed forward NNs (F, G and H in the paper). We assume that all three NNs have the
        same hyper-parameters: num_hidden_layers, hidden_layer_width and hidden_layer_activation. That is, 
        F, G and H have the same structure and activations. Their actual weights are different, though. H has a
        separate softmax layer at the end.
        '''
        super(DecomposableAttentionEntailment, self).build(input_shape)
        # input_shape is a list containing the shapes of the two inputs.
        # Both sentences should be of the same length to share compare weights.
        assert input_shape[0][1] == input_shape[1][1]
        sentence_length = input_shape[0][-2]
        input_dim = input_shape[0][-1]
        # pylint: disable=attribute-defined-outside-init
        self.attend_weights = []  # weights related to F
        self.compare_weights = []  # weights related to G
        self.aggregate_weights = []  # weights related to H
        attend_input_dim = input_dim
        compare_input_dim = 2 * input_dim
        aggregate_input_dim = self.hidden_layer_width * 2
        for i in range(self.num_hidden_layers):
            self.attend_weights.append(self.init((attend_input_dim, self.hidden_layer_width),
                                                 name='%s_attend_%d' % (self.name, i)))
            self.compare_weights.append(self.init((compare_input_dim, self.hidden_layer_width),
                                                  name='%s_compare_%d' % (self.name, i)))
            self.aggregate_weights.append(self.init((aggregate_input_dim, self.hidden_layer_width),
                                                    name='%s_aggregate_%d' % (self.name, i)))
            attend_input_dim = self.hidden_layer_width
            compare_input_dim = self.hidden_layer_width
            aggregate_input_dim = self.hidden_layer_width
        self.trainable_weights = self.attend_weights + self.compare_weights + self.aggregate_weights
        self.scorer = self.init((self.hidden_layer_width, 2), name='%s_score' % self.name)
        self.trainable_weights.append(self.scorer)

    def compute_mask(self, x, mask=None):
        # pylint: disable=unused-argument
        return None

    def get_output_shape_for(self, input_shape):
        # (batch_size, 2)
        return (input_shape[0][0], 2)  # returns T/F probabilities.

    @staticmethod
    def _apply_feed_forward(input_tensor, weights, activation):
        current_tensor = input_tensor
        for weight in weights:
            current_tensor = activation(K.dot(current_tensor, weight))
        return current_tensor

    def call(self, x, mask=None):
        # premise_length = hypothesis_length in the following lines, but the names are kept separate to keep
        # track of the axes being normalized.
        premise_embedding, hypothesis_embedding = x
        # (batch_size, premise_length), (batch_size, hypothesis_length)
        premise_mask, hypothesis_mask = mask
        if premise_mask is not None:
            premise_embedding = switch(K.expand_dims(premise_mask), premise_embedding,
                                       K.zeros_like(premise_embedding))
        if hypothesis_mask is not None:
            hypothesis_embedding = switch(K.expand_dims(hypothesis_mask), hypothesis_embedding,
                                          K.zeros_like(hypothesis_embedding))
        activation = activations.get(self.hidden_layer_activation)
        # (batch_size, premise_length, hidden_dim)
        projected_premise = self._apply_feed_forward(premise_embedding, self.attend_weights, activation)
        # (batch_size, hypothesis_length, hidden_dim)
        projected_hypothesis = self._apply_feed_forward(hypothesis_embedding, self.attend_weights, activation)
        # (batch_size, premise_length, hypothesis_length)
        unnormalized_attention = K.batch_dot(projected_premise, projected_hypothesis, axes=(2, 2))
        (batch_size, premise_length, hypothesis_length) = K.shape(unnormalized_attention)

        ## Step 1: Attend
        # (batch_size, hypothesis_length, premise_length)
        reshaped_attention = K.permute_dimensions(unnormalized_attention, (0, 2, 1))
        flattened_unnormalized_attention = K.batch_flatten(unnormalized_attention)
        flattened_reshaped_attention = K.batch_flatten(reshaped_attention)
        if premise_mask is None and hypothesis_mask is None:
            # (batch_size * premise_length, hypothesis_length)
            flattened_p2h_attention = K.softmax(flattened_unnormalized_attention)
            # (batch_size * hypothesis_length, premise_length)
            flattened_h2p_attention = K.softmax(flattened_reshaped_attention)
        else:
            # We have to compute alignment masks. All those elements that correspond to a masked word in
            # either the premise or the hypothesis need to be masked.
            # (batch_size * premise_length, hypothesis_length)
            p2h_mask = K.batch_flatten(K.expand_dims(premise_mask, dim=-1) * K.expand_dims(hypothesis_mask, dim=1))
            # (batch_size * hypothesis_length, premise_length)
            h2p_mask = K.batch_flatten(K.expand_dims(premise_mask, dim=1) * K.expand_dims(hypothesis_mask, dim=-1))
            flattened_p2h_attention = masked_softmax(flattened_unnormalized_attention, p2h_mask)
            flattened_h2p_attention = masked_softmax(flattened_reshaped_attention, h2p_mask)
        # (batch_size, premise_length, hypothesis_length)
        p2h_attention = K.reshape(flattened_p2h_attention, (batch_size, premise_length, hypothesis_length))
        # (batch_size, hypothesis_length, premise_length)
        h2p_attention = K.reshape(flattened_h2p_attention, (batch_size, hypothesis_length, premise_length))
        # beta in the paper (equation 2)
        # sum((batch_size, premise_length, hyp_length, embed_dim), axis=2) = (batch_size, premise_length, emb_dim)
        p2h_alignments = K.sum(K.expand_dims(p2h_attention, dim=-1) * K.expand_dims(hypothesis_embedding, dim=1),
                               axis=2)
        # alpha in the paper (equation 2)
        # sum((batch_size, hyp_length, premise_length, embed_dim), axis=2) = (batch_size, hyp_length, emb_dim)
        h2p_alignments = K.sum(K.expand_dims(h2p_attention, dim=-1) * K.expand_dims(premise_embedding, dim=1),
                               axis=2)

        ## Step 2: Compare
        # Concatenate premise embedding and its alignments with hypothesis
        premise_comparison_input = K.concatenate([premise_embedding, p2h_alignments])
        hypothesis_comparison_input = K.concatenate([hypothesis_embedding, h2p_alignments])
        # Equation 3 in the paper.
        compared_premise = self._apply_feed_forward(premise_comparison_input, self.compare_weights, activation)
        compared_hypothesis = self._apply_feed_forward(hypothesis_comparison_input, self.compare_weights,
                                                       activation)

        ## Step 3: Aggregate
        # Equations 4 and 5.
        # (batch_size, hidden_dim * 2)
        aggregated_input = K.concatenate([K.sum(compared_premise, axis=1), K.sum(compared_hypothesis, axis=1)])
        # (batch_size, hidden_dim)
        input_to_scorer = self._apply_feed_forward(aggregated_input, self.aggregate_weights, activation)
        # (batch_size, 2)
        scores = K.softmax(K.dot(input_to_scorer, self.scorer))
        return scores


def split_combiner_inputs(x, encoding_dim: int):  # pylint: disable=invalid-name
    sentence_encoding = x[:, :encoding_dim]
    current_memory = x[:, encoding_dim:2*encoding_dim]
    attended_knowledge = x[:, 2*encoding_dim:]
    return sentence_encoding, current_memory, attended_knowledge


class MemoryOnlyCombiner(Layer):
    """
    This "combiner" just selects the current memory and returns it.
    """
    def __init__(self, encoding_dim, name="entailment_combiner", **kwargs):
        super(MemoryOnlyCombiner, self).__init__(name=name, **kwargs)
        self.encoding_dim = encoding_dim

    def call(self, x, mask=None):
        _, current_memory, _ = split_combiner_inputs(x, self.encoding_dim)
        return current_memory

    def get_output_shape_for(self, input_shape):
        return (input_shape[0], self.encoding_dim)

    def get_config(self):
        base_config = super(MemoryOnlyCombiner, self).get_config()
        config = {'encoding_dim': self.encoding_dim}
        config.update(base_config)
        return config


class HeuristicMatchingCombiner(Layer):
    """
    This class is an implementation of the heuristic matching algorithm proposed in the following paper:
    "Natural Language Inference by Tree-Based Convolution and Heuristic Matching", Mou et al, ACL 2016.
    """
    def __init__(self, encoding_dim, name="entailment_combiner", **kwargs):
        super(HeuristicMatchingCombiner, self).__init__(name=name, **kwargs)
        self.encoding_dim = encoding_dim

    def call(self, x, mask=None):
        sentence_encoding, current_memory, _ = split_combiner_inputs(x, self.encoding_dim)
        sentence_memory_product = sentence_encoding * current_memory
        sentence_memory_diff = sentence_encoding - current_memory
        return K.concatenate([sentence_encoding,
                              current_memory,
                              sentence_memory_product,
                              sentence_memory_diff])

    def get_output_shape_for(self, input_shape):
        # There are four components we've concatenated above: (1) the sentence encoding, (2) the
        # current memory, (3) the elementwise product of these two, and (4) their difference.  Each
        # of these has dimension `self.encoding_dim`.
        return (input_shape[0], self.encoding_dim * 4)

    def get_config(self):
        base_config = super(HeuristicMatchingCombiner, self).get_config()
        config = {'encoding_dim': self.encoding_dim}
        config.update(base_config)
        return config


entailment_models = {  # pylint: disable=invalid-name
        'true_false_mlp': TrueFalseEntailmentModel,
        'multiple_choice_mlp': MultipleChoiceEntailmentModel,
        'question_answer_mlp': QuestionAnswerEntailmentModel,
        'decomposable_attention': DecomposableAttentionEntailment,
        }

entailment_input_combiners = {  # pylint: disable=invalid-name
        'memory_only': MemoryOnlyCombiner,
        'heuristic_matching': HeuristicMatchingCombiner,
        }
