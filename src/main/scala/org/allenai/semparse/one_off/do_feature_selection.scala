package org.allenai.semparse.one_off

import edu.cmu.ml.rtw.users.matt.util.FileUtil

import scala.collection.mutable

object do_feature_selection {
  val fileUtil = new FileUtil

  def main(args: Array[String]) {
    println("Selecting MID features")
    featureSelectionFromFile(
      "/home/mattg/pra/results/semparse/mids/unknown/training_matrix.tsv",
      "word-graph-features",
      "/home/mattg/clone/tacl2015-factorization/data/mid_features"
    )
    println("Selecting MID pair features")
    featureSelectionFromFile(
      "/home/mattg/pra/results/semparse/mid_pairs/unknown/training_matrix.tsv",
      "word-rel-graph-features",
      "/home/mattg/clone/tacl2015-factorization/data/mid_pair_features"
    )
  }

  def featureSelectionFromFile(infile: String, dictionaryName: String, outfile: String) {
    println("Reading features from file")
    val (featuresForKey, featureCounts) = readFeaturesFromFile(infile)
    printTopKeys(featuresForKey, 10)
    println("Selecting features")
    val keptFeatures = selectFeatures(featureCounts)
    println(s"Kept ${keptFeatures.size} features")
    println("Filtering features")
    val filteredFeatures = filterFeatures(featuresForKey, keptFeatures)
    printTopKeys(filteredFeatures, 10)
    println("Outputting feature matrix")
    outputFeatureMatrix(filteredFeatures, outfile + ".tsv")
    println("Outputting feature dictionary")
    outputFeatureDictionary(filteredFeatures, dictionaryName, outfile + "_list.txt")
  }

  def readFeaturesFromFile(infile: String) = {
    val features = fileUtil.getLineIterator(infile).grouped(1024).flatMap(lines => {
      lines.par.map(line => {
        val (keyStr, featuresStr) = line.splitAt(line.indexOf('\t'))
        val key = keyStr.trim
        val features = featuresStr.trim.split(" -#- ").map(_.replace(",1.0", ""))
        (key, features.toSeq)
      })
    }).toSeq
    val featuresForKey = features.toMap.seq
    val featureCounts = features.flatMap(_._2).groupBy(identity).mapValues(_.size).seq
    (featuresForKey, featureCounts)
  }

  def printTopKeys(featuresForKey: Map[String, Seq[String]], topK: Int) {
    val topKeys = featuresForKey.keys.toSeq.sortBy(key => -featuresForKey(key).size).take(topK)
    println("Top keys:")
    for (key <- topKeys) {
      println(s"${key} -> ${featuresForKey(key).size}")
    }
  }

  def printFeatureCountHistogram(featureCounts: Map[String, Int]) {
    val featureCountHistogram = new mutable.HashMap[Int, Int].withDefaultValue(0)
    for (featureCount <- featureCounts) {
      val count = featureCount._2
      featureCountHistogram.update(count, featureCountHistogram(count) + 1)
    }
    val counts = featureCountHistogram.keys.toSeq.sortBy(key => (featureCountHistogram(key), -key))
    println("Feature histogram:")
    for (count <- counts) {
      println(s"${count} -> ${featureCountHistogram(count)}")
    }
  }

  def selectFeatures(featureCounts: Map[String, Int]): Set[String] = {
    val features = featureCounts.keySet
    features.toSeq.sortBy(x => -featureCounts(x)).take(20000).toSet
    //features.par.filter(feature => featureCounts(feature) > 5).toSet.seq
  }

  def filterFeatures(featuresForKey: Map[String, Seq[String]], keptFeatures: Set[String]) = {
    featuresForKey.par.mapValues(_.filter(feature => keptFeatures.contains(feature)) ++ Seq("bias")).seq.toMap
  }

  def outputFeatureMatrix(featuresForKey: Map[String, Seq[String]], outfile: String) {
    val writer = fileUtil.getFileWriter(outfile)
    val seen_features = new mutable.HashSet[String]
    for (keyFeatures <- featuresForKey) {
      val key = keyFeatures._1
      val features = keyFeatures._2
      writer.write(key)
      writer.write("\t")
      for ((feature, i) <- features.zipWithIndex) {
        writer.write(feature)
        seen_features.add(feature)
        if (i < features.size - 1) writer.write(" -#- ")
      }
      writer.write("\n")
    }
    writer.close()
    println(s"Saw ${seen_features.size} features")
  }

  def outputFeatureDictionary(featuresForKey: Map[String, Seq[String]], dictionaryName: String, outfile: String) {
    val writer = fileUtil.getFileWriter(outfile)
    val features = featuresForKey.flatMap(_._2).toSet
    writer.write(s"(define ${dictionaryName} (list\n")
    for (feature <- features) {
      writer.write("\"")
      writer.write(feature)
      writer.write("\"\n")
    }
    writer.write("))\n")
    writer.close()
  }
}
