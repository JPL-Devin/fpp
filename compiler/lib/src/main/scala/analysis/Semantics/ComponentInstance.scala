package fpp.compiler.analysis

import fpp.compiler.ast._
import fpp.compiler.util._

/** An FPP component instance */
final case class ComponentInstance(
  aNode: Ast.Annotated[AstNode[Ast.DefComponentInstance]],
  qualifiedName: Name.Qualified,
  component: Component,
  baseId: BigInt,
  maxId: BigInt,
  file: Option[String],
  queueSize: Option[BigInt],
  queuePriorities: Option[List[(BigInt, BigInt)]],
  stackSize: Option[BigInt],
  priority: Option[BigInt],
  cpu: Option[BigInt],
  initSpecifierMap: Map[Int, InitSpecifier] = Map()
) extends Ordered[ComponentInstance] {

  override def toString = qualifiedName.toString

  /** Adds an init specifier */
  def addInitSpecifier(initSpecifier: InitSpecifier):
  Result.Result[ComponentInstance] = {
    val phase = initSpecifier.phase
    initSpecifierMap.get(initSpecifier.phase) match {
      case Some(prevSpec) =>
        val loc = initSpecifier.getLoc
        val prevLoc = prevSpec.getLoc
        Left(SemanticError.DuplicateInitSpecifier(phase, loc, prevLoc))
      case None =>
        val map = initSpecifierMap + (phase -> initSpecifier)
        Right(this.copy(initSpecifierMap = map))
    }
  }

  /** Gets the qualified name of the component instance */
  def getQualifiedName = qualifiedName

  /** Gets the unqualified name of the component instance */
  def getUnqualifiedName = aNode._2.data.name

  /** Gets the location of the component instance */
  def getLoc: Location = Locations.get(aNode._2.id)

  def getInterface: PortInterface = component.portInterface

  override def compare(that: ComponentInstance) =
    this.qualifiedName.toString.compare(that.qualifiedName.toString)

}

object ComponentInstance {

  /** Creates a component instance from a component instance definition */
  def fromDefComponentInstance(
    a: Analysis,
    aNode: Ast.Annotated[AstNode[Ast.DefComponentInstance]]
  ): Result.Result[ComponentInstance] = {
    val node = aNode._2
    val data = node.data
    for {
      component <- a.getComponent(data.component.id)
      componentKind <- Right(component.aNode._2.data.kind)
      baseId <- a.getNonnegativeBigIntValue(data.baseId.id)
      file <- Right(data.file.map(getFile))
      queueSize <- getQueueSize(
        a,
        data.name,
        Locations.get(node.id),
        componentKind,
        data.queueSize
      )
      queuePriorities <- getQueuePriorities(
        a,
        data.name,
        Locations.get(node.id),
        componentKind,
        component,
        data.queuePriorities
      )
      stackSize <- getActiveAttribute(
        data.name,
        componentKind
      )(
        "stack size",
        a.getNonnegativeBigIntValueOpt,
        data.stackSize
      )
      priority <- getActiveAttribute(
        data.name,
        componentKind,
      )(
        "priority",
        node => Right(a.getBigIntValueOpt(node)),
        data.priority
      )
      cpu <- getActiveAttribute(
        data.name,
        componentKind,
      )(
        "CPU affinity",
        node => Right(a.getBigIntValueOpt(node)),
        data.cpu
      )
    }
    yield {
      val maxId = baseId + component.getMaxId
      val symbol = Symbol.ComponentInstance(aNode)
      val qualifiedName = a.getQualifiedName(symbol)
      ComponentInstance(
        aNode,
        qualifiedName,
        component,
        baseId,
        maxId,
        file,
        queueSize,
        queuePriorities,
        stackSize,
        priority,
        cpu
      )
    }
  }

  /** Construct an invalid instance error */
  private def invalid(
    name: String,
    loc: Location,
    msg: String
  ) = Left(
    SemanticError.InvalidDefComponentInstance(name, loc, msg)
  )

  /** Gets the file */
  private def getFile(node: AstNode[String]): String = {
    val loc = Locations.get(node.id)
    val javaPath = loc.getRelativePath(node.data)
    File.Path(javaPath).toString
  }

  /** Gets the queue size */
  private def getQueueSize(
    a: Analysis,
    name: String,
    loc: Location,
    componentKind: Ast.ComponentKind,
    nodeOpt: Option[AstNode[Ast.Expr]]
  ): Result.Result[Option[BigInt]] = {
    (componentKind, nodeOpt) match {
      case (Ast.ComponentKind.Passive, Some(node)) => invalid(
        name,
        Locations.get(node.id),
        "passive component may not have queue size"
      )
      case (_, Some(_)) => a.getNonnegativeBigIntValueOpt(nodeOpt)
      case (Ast.ComponentKind.Passive, None) => Right(None)
      case _ => invalid(
        name,
        loc,
        s"$componentKind component must have queue size"
      )
    }
  }

  /** The maximum number of queue priorities */
  val maxQueuePriorities = 32

  /** Gets the queue priorities */
  private def getQueuePriorities(
    a: Analysis,
    name: String,
    loc: Location,
    componentKind: Ast.ComponentKind,
    component: Component,
    entriesOpt: Option[List[AstNode[Ast.QueuePriorityEntry]]]
  ): Result.Result[Option[List[(BigInt, BigInt)]]] = {
    (componentKind, entriesOpt) match {
      case (Ast.ComponentKind.Passive, Some(entries)) => invalid(
        name,
        entries.headOption.map(e => Locations.get(e.id)).getOrElse(loc),
        "passive component may not have queue priorities"
      )
      case (_, Some(entries)) =>
        val hasSerialAsyncInput = component.portMap.values.exists {
          case g: PortInstance.General => (g.getType, g.kind) match {
            case (
              Some(PortInstance.Type.Serial),
              PortInstance.General.Kind.AsyncInput(_, _)
            ) => true
            case _ => false
          }
          case _ => false
        }
        for {
          _ <-
            if (!hasSerialAsyncInput) Right(())
            else invalid(
              name,
              entries.headOption.map(e => Locations.get(e.id)).getOrElse(loc),
              "queue priorities may not be used with a component that has serial async input ports"
            )
          resolved <- Result.map(
            entries,
            (entry: AstNode[Ast.QueuePriorityEntry]) => for {
              priority <- a.getNonnegativeBigIntValue(entry.data.priority.id)
              _ <-
                if (priority < maxQueuePriorities) Right(())
                else invalid(
                  name,
                  Locations.get(entry.data.priority.id),
                  s"queue priority $priority is out of range [0, ${maxQueuePriorities - 1}]"
                )
              size <- a.getNonnegativeBigIntValue(entry.data.size.id)
            } yield (entry, priority, size)
          )
          _ <- checkForDuplicates(name, resolved)
          _ <- checkAgainstUsedPriorities(name, loc, component, resolved)
        } yield Some(resolved.map { case (_, priority, size) => (priority, size) })
      case (_, None) => Right(None)
    }
  }

  /** Checks for duplicate queue priority entries */
  private def checkForDuplicates(
    name: String,
    resolved: List[(AstNode[Ast.QueuePriorityEntry], BigInt, BigInt)]
  ): Result.Result[Unit] =
    Result.foldLeft (resolved) (Map[BigInt, AstNode[Ast.QueuePriorityEntry]]()) {
      case (map, (entry, priority, _)) => map.get(priority) match {
        case Some(_) => invalid(
          name,
          Locations.get(entry.data.priority.id),
          s"duplicate entry for queue priority $priority"
        )
        case None => Right(map + (priority -> entry))
      }
    }.map(_ => ())

  /** Checks queue priority entries against the priorities used by the
   *  component */
  private def checkAgainstUsedPriorities(
    name: String,
    loc: Location,
    component: Component,
    resolved: List[(AstNode[Ast.QueuePriorityEntry], BigInt, BigInt)]
  ): Result.Result[Unit] = {
    val usedPriorities = component.getUsedQueuePriorities
    val specifiedPriorities = resolved.map(_._2).toSet
    val missing = usedPriorities.diff(specifiedPriorities)
    val unused = resolved.filter { case (_, priority, _) =>
      !usedPriorities.contains(priority)
    }
    if (missing.nonEmpty) invalid(
      name,
      resolved.headOption.map(r => Locations.get(r._1.id)).getOrElse(loc),
      s"queue priorities block is missing entries for priorities used by the component: ${missing.toList.sorted.mkString(", ")}"
    )
    else unused match {
      case (entry, priority, _) :: _ => invalid(
        name,
        Locations.get(entry.data.priority.id),
        s"queue priority $priority is not used by the component"
      )
      case Nil => Right(())
    }
  }

  /** Get an attribute for an active component */
  private def getActiveAttribute(
    name: String,
    componentKind: Ast.ComponentKind
  )
  (
    kind: String,
    getValue: Option[AstNode[Ast.Expr]] => Result.Result[Option[BigInt]],
    nodeOpt: Option[AstNode[Ast.Expr]]
  ): Result.Result[Option[BigInt]] =
    (componentKind, nodeOpt) match {
      case (Ast.ComponentKind.Active, Some(_)) => getValue(nodeOpt)
      case (_, Some(node)) => invalid(
        name,
        Locations.get(node.id),
        s"$componentKind component may not have $kind"
      )
      case (_, None) => Right(None)
    }

}
