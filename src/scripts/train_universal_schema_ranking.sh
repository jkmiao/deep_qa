#!/bin/bash -e

DATA_DIR=/home/jayantk/data/universal_schema/clueweb/lf/training_all/joint_newrules2/ranking/
MODEL_DIR=/home/jayantk/data/universal_schema/clueweb/models/100114/

ENTITY_FILE=$DATA_DIR/entities.txt
WORD_FILE=$DATA_DIR/words.txt
LF_FILE=$DATA_DIR/lf.txt

EPOCHS=100
L2_REGULARIZATION=0.0001
L2_REGULARIZATION_FREQ=0.0001
MODEL_NAME="wcount=5_d=300_iter=100_l2=1e-4_ranking"
MODEL_OUTPUT=$MODEL_DIR/out_$MODEL_NAME.ser
LOG_OUTPUT=$MODEL_DIR/log_$MODEL_NAME.txt

mkdir -p $MODEL_DIR

java -cp 'lib/*' -Xmx80000M com.jayantkrish.jklol.lisp.cli.AmbLisp --optEpochs $EPOCHS --optL2Regularization $L2_REGULARIZATION --optL2RegularizationFrequency $L2_REGULARIZATION_FREQ --args $MODEL_OUTPUT src/lisp/universal_schema/environment.lisp $ENTITY_FILE $WORD_FILE $LF_FILE src/lisp/universal_schema/universal_schema.lisp src/lisp/universal_schema/train_universal_schema_ranking.lisp > $LOG_OUTPUT
